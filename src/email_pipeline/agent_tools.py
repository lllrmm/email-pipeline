"""Restricted tools used by the OpenCode single-email agent."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import socket
import subprocess
import tempfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import yaml

from .mime_extract import extract_attachment_text, normalize_space, secure_write_bytes, secure_write_text
from .daily_schema import validation_result
from .mail_identity import MailIdentityIndex
from .imap_backend import fetch_raw


MAX_LINK_BYTES = 2 * 1024 * 1024
ALLOWED_LINK_TYPES = ("text/html", "text/plain", "application/json")
URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.I)


def workspace_path(value: str) -> Path:
    workspace = Path(value).expanduser().resolve()
    request_path = workspace / "request.json"
    if not workspace.is_dir() or not request_path.is_file():
        raise RuntimeError("not a mail agent workspace")
    return workspace


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def decode_part(part: Message) -> str:
    try:
        value = part.get_content()
        if isinstance(value, str):
            return value
    except Exception:
        pass
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def header_text(message: Message, name: str) -> str:
    return ", ".join(str(value) for value in message.get_all(name, [])).strip()


def persist_message_metadata(index: MailIdentityIndex, pipeline_id: str, data: bytes) -> None:
    message = BytesParser(policy=policy.default).parsebytes(data, headersonly=True)
    date_header = header_text(message, "Date")
    index.update_metadata(
        pipeline_id,
        date_header=date_header or None,
        subject=header_text(message, "Subject") or None,
        sender=header_text(message, "From") or None,
        recipients=header_text(message, "To") or None,
        cc=header_text(message, "Cc") or None,
        bcc=header_text(message, "Bcc") or None,
        reply_to=header_text(message, "Reply-To") or None,
        in_reply_to=header_text(message, "In-Reply-To") or None,
        references_header=header_text(message, "References") or None,
    )


def command_fetch(workspace: Path) -> dict[str, Any]:
    request = load_json(workspace / "request.json")
    pipeline_id = str(request.get("pipeline_id") or "").strip()
    database = request.get("index_database")
    if not pipeline_id or not database:
        raise RuntimeError("request.json requires pipeline_id and index_database")
    identity = MailIdentityIndex(Path(database)).lookup_pipeline_id(pipeline_id)
    if not identity or not identity.get("locations"):
        raise RuntimeError(f"no IMAP location found for pipeline id: {pipeline_id}")
    eml_path = workspace / "message.eml"
    index = MailIdentityIndex(Path(database))
    if eml_path.is_file():
        data = eml_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        index.set_eml_sha256(pipeline_id, digest)
        persist_message_metadata(index, pipeline_id, data)
        return {
            "status": "cached",
            "pipeline_id": pipeline_id,
            "path": "message.eml",
            "size": len(data),
            "sha256": digest,
        }
    last_error = ""
    for location in identity["locations"]:
        folder = str(location.get("folder") or "").strip()
        uidvalidity = location.get("uidvalidity")
        uid = location.get("imap_uid")
        if not folder or uidvalidity is None or uid is None:
            continue
        for attempt in range(3):
            try:
                hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
                config_path = hermes_home / "scripts" / "daily-mail-pipeline.yaml"
                config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                data = fetch_raw(config, folder, int(uidvalidity), int(uid))
                secure_write_bytes(eml_path, data)
                digest = hashlib.sha256(data).hexdigest()
                index.set_eml_sha256(pipeline_id, digest)
                persist_message_metadata(index, pipeline_id, data)
                return {
                    "status": "fetched",
                    "pipeline_id": pipeline_id,
                    "path": "message.eml",
                    "size": len(data),
                    "sha256": digest,
                }
            except Exception as exc:
                last_error = str(exc)[:500]
            if attempt < 2:
                import time
                time.sleep((attempt + 1) * 5)
    raise RuntimeError(f"IMAP fetch failed: {last_error}")


def command_unpack(workspace: Path) -> dict[str, Any]:
    eml_path = workspace / "message.eml"
    if not eml_path.is_file():
        raise RuntimeError("call mail_fetch before mail_unpack")
    message = BytesParser(policy=policy.default).parsebytes(eml_path.read_bytes())
    parts_dir = workspace / "parts"
    attachments_dir = workspace / "attachments"
    parts_dir.mkdir(mode=0o700, exist_ok=True)
    attachments_dir.mkdir(mode=0o700, exist_ok=True)
    parts: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []

    for index, part in enumerate(message.walk(), 1):
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        content_id = (part.get("Content-ID") or "").strip("<>") or None
        payload = part.get_payload(decode=True) or b""
        is_attachment = bool(filename) or disposition in {"attachment", "inline"} or content_type.startswith("image/")
        if is_attachment:
            safe_name = Path(filename or f"part-{index}{mimetypes.guess_extension(content_type) or '.bin'}").name
            safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", safe_name).strip(" .") or f"part-{index}.bin"
            attachment_id = f"attachment-{len(attachments) + 1}"
            stored_name = f"{attachment_id}-{safe_name[:100]}"
            stored_path = attachments_dir / stored_name
            blocked = Path(safe_name).suffix.lower() in {
                ".exe", ".msi", ".bat", ".cmd", ".ps1", ".sh", ".js", ".jar", ".scr", ".com",
            }
            if not blocked:
                secure_write_bytes(stored_path, payload)
            attachments.append({
                "id": attachment_id,
                "filename": safe_name,
                "path": str(stored_path.relative_to(workspace)) if not blocked else None,
                "content_type": content_type,
                "disposition": disposition,
                "content_id": content_id,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "blocked": blocked,
            })
            continue

        part_id = f"part-{len(parts) + 1}"
        extension = ".html" if content_type == "text/html" else ".txt"
        part_path = parts_dir / f"{part_id}{extension}"
        text = decode_part(part)
        secure_write_text(part_path, text)
        parts.append({
            "id": part_id,
            "path": str(part_path.relative_to(workspace)),
            "content_type": content_type,
            "charset": part.get_content_charset(),
            "size": len(text.encode("utf-8", errors="replace")),
        })

    manifest = {
        "pipeline_id": load_json(workspace / "request.json").get("pipeline_id"),
        "subject": str(message.get("Subject") or ""),
        "from": str(message.get("From") or ""),
        "to": str(message.get("To") or ""),
        "cc": str(message.get("Cc") or ""),
        "date": str(message.get("Date") or ""),
        "message_id_header": str(message.get("Message-ID") or ""),
        "parts": parts,
        "attachments": attachments,
    }
    secure_write_text(workspace / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"status": "unpacked", "manifest_path": "manifest.json", **manifest}


def command_extract_attachment(workspace: Path, attachment_id: str) -> dict[str, Any]:
    manifest = load_json(workspace / "manifest.json")
    attachment = next((item for item in manifest.get("attachments", []) if item.get("id") == attachment_id), None)
    if not attachment:
        raise RuntimeError(f"unknown attachment id: {attachment_id}")
    if attachment.get("blocked") or not attachment.get("path"):
        return {"status": "blocked", "attachment_id": attachment_id}
    source_path = (workspace / attachment["path"]).resolve()
    if workspace not in source_path.parents:
        raise RuntimeError("attachment path escaped workspace")
    text, warnings = extract_attachment_text(
        source_path,
        str(attachment.get("filename") or source_path.name),
        str(attachment.get("content_type") or "application/octet-stream"),
        50000,
    )
    text_path = source_path.with_suffix(source_path.suffix + ".txt")
    if text:
        secure_write_text(text_path, text)
        attachment["text_path"] = str(text_path.relative_to(workspace))
    attachment["extraction_warnings"] = warnings
    secure_write_text(workspace / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {
        "status": "extracted" if text else "no_text",
        "attachment_id": attachment_id,
        "text_path": attachment.get("text_path"),
        "characters": len(text),
        "warnings": warnings,
        "excerpt": text[:1000],
    }


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.stack: list[dict[str, Any]] = []
        self.base_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag.lower() == "base" and values.get("href"):
            self.base_url = values["href"]
        if tag.lower() == "a" and values.get("href"):
            self.stack.append({"url": values["href"], "text": []})

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.stack:
            item = self.stack.pop()
            raw_url = item["url"].strip()
            self.links.append({
                "raw_url": raw_url,
                "url": urljoin(self.base_url, raw_url) if self.base_url else raw_url,
                "text": normalize_space("".join(item["text"])),
            })

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1]["text"].append(data)


def command_extract_links(workspace: Path, source_id: str) -> dict[str, Any]:
    manifest = load_json(workspace / "manifest.json")
    source = next((item for item in manifest.get("parts", []) if item.get("id") == source_id), None)
    if not source:
        source = next((item for item in manifest.get("attachments", []) if item.get("id") == source_id), None)
        if source and source.get("text_path"):
            source = {**source, "path": source["text_path"], "content_type": "text/plain"}
    if not source or not source.get("path"):
        raise RuntimeError(f"source is not readable text: {source_id}")
    path = (workspace / source["path"]).resolve()
    text = path.read_text(encoding="utf-8", errors="replace")
    links: list[dict[str, Any]] = []
    if source.get("content_type") == "text/html" or path.suffix.lower() in {".html", ".htm"}:
        parser = LinkParser()
        parser.feed(text)
        raw_links = parser.links
    else:
        raw_links = [{"raw_url": match.group(0), "url": match.group(0), "text": match.group(0)} for match in URL_RE.finditer(text)]
    registry_path = workspace / "links.json"
    registry = load_json(registry_path) if registry_path.exists() else {"links": []}
    existing = {(item.get("source_id"), item.get("raw_url")): item for item in registry.get("links", [])}
    for item in raw_links:
        key = (source_id, item["raw_url"])
        if key in existing:
            links.append(existing[key])
            continue
        link = {
            "id": f"link-{len(registry['links']) + 1}",
            "source_id": source_id,
            **item,
        }
        registry["links"].append(link)
        existing[key] = link
        links.append(link)
    secure_write_text(registry_path, json.dumps(registry, ensure_ascii=False, indent=2))
    return {"status": "extracted", "source_id": source_id, "links": links}


def validate_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("only absolute HTTPS URLs may be inspected")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise RuntimeError("link resolves to a non-public address")


def command_inspect_link(workspace: Path, link_id: str) -> dict[str, Any]:
    registry = load_json(workspace / "links.json")
    link = next((item for item in registry.get("links", []) if item.get("id") == link_id), None)
    if not link:
        raise RuntimeError(f"unknown link id: {link_id}")
    current_url = str(link.get("url") or "")
    redirects: list[str] = []
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "Hermes-Mail-Link-Inspector/1.0", "Accept": "text/html,text/plain,application/json"})
    response = None
    for _ in range(4):
        validate_public_https(current_url)
        response = session.get(current_url, timeout=15, allow_redirects=False, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            if not location:
                break
            current_url = urljoin(current_url, location)
            redirects.append(current_url)
            continue
        break
    if response is None:
        raise RuntimeError("link fetch failed")
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if not any(content_type.startswith(value) for value in ALLOWED_LINK_TYPES):
        raise RuntimeError(f"unsupported link content type: {content_type}")
    data = bytearray()
    for chunk in response.iter_content(65536):
        data.extend(chunk)
        if len(data) > MAX_LINK_BYTES:
            raise RuntimeError("link response exceeded size limit")
    text = bytes(data).decode(response.encoding or "utf-8", errors="replace")
    if content_type == "text/html":
        from .mime_extract import html_to_text
        text, _, _, warnings = html_to_text(text)
    else:
        warnings = []
    links_dir = workspace / "links"
    links_dir.mkdir(mode=0o700, exist_ok=True)
    text_path = links_dir / f"{link_id}.txt"
    secure_write_text(text_path, text[:100000])
    return {
        "status": response.status_code,
        "link_id": link_id,
        "final_url": current_url,
        "redirects": redirects,
        "content_type": content_type,
        "text_path": str(text_path.relative_to(workspace)),
        "warnings": warnings,
        "excerpt": text[:1500],
    }


def command_validate_daily_summary(summary_json: str) -> dict[str, Any]:
    try:
        value = json.loads(summary_json)
    except json.JSONDecodeError as exc:
        return {"valid": False, "errors": [f"invalid JSON: {exc}"]}
    return validation_result(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["fetch", "unpack", "extract-attachment", "extract-links", "inspect-link", "validate-daily-summary"])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--id")
    parser.add_argument("--json")
    args = parser.parse_args()
    if args.command == "validate-daily-summary":
        workspace = Path(args.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise RuntimeError("workspace directory does not exist")
    else:
        workspace = workspace_path(args.workspace)
    if args.command == "fetch":
        result = command_fetch(workspace)
    elif args.command == "unpack":
        result = command_unpack(workspace)
    elif args.command == "extract-attachment":
        result = command_extract_attachment(workspace, str(args.id or ""))
    elif args.command == "extract-links":
        result = command_extract_links(workspace, str(args.id or ""))
    elif args.command == "inspect-link":
        result = command_inspect_link(workspace, str(args.id or ""))
    else:
        result = command_validate_daily_summary(str(args.json or ""))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
