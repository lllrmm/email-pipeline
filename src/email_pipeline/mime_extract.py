"""Safe MIME, HTML, image-link, and attachment extraction."""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET


PLACEHOLDERS = (
    re.compile(r"view (?:this )?email in (?:your )?browser", re.I),
    re.compile(r"view (?:the )?(?:message|newsletter) online", re.I),
    re.compile(r"click here to view", re.I),
    re.compile(r"在浏览器中查看", re.I),
    re.compile(r"网页版", re.I),
)
URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.I)
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".ics", ".log"}
BLOCKED_EXTENSIONS = {
    ".app", ".bat", ".cmd", ".com", ".dll", ".dmg", ".exe", ".hta",
    ".iso", ".jar", ".js", ".jse", ".lnk", ".msi", ".ps1", ".scr",
    ".sh", ".vbe", ".vbs", ".wsf",
}
MAX_OFFICE_MEMBERS = 5000
MAX_OFFICE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO = 200
MAX_XML_BYTES = 10 * 1024 * 1024


def normalize_space(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def safe_url(value: str | None) -> str | None:
    if not value:
        return None
    value = html.unescape(value).strip()
    if urlparse(value).scheme.lower() not in {"http", "https", "mailto"}:
        return None
    return value


def dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in links:
        url = safe_url(item.get("url"))
        if not url:
            continue
        source = item.get("source", "link")
        key = (url, source)
        if key in seen:
            continue
        seen.add(key)
        text = normalize_space(item.get("text", ""))[:500]
        result.append({"text": text or url, "url": url, "source": source})
    return result


class VisibleHTMLParser(HTMLParser):
    """Extract visible content without loading any remote resource."""

    SKIP = {"head", "script", "style", "noscript", "svg", "template"}
    BLOCK = {
        "address", "article", "aside", "blockquote", "br", "div", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
        "nav", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self.images: list[dict[str, Any]] = []
        self.skip_depth = 0
        self.anchors: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): (value or "") for key, value in attrs}
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self.anchors.append({"url": safe_url(attrs_map.get("href")), "text": [], "has_image": False})
        elif tag == "img":
            src = attrs_map.get("src", "").strip()
            alt = normalize_space(attrs_map.get("alt", "") or attrs_map.get("title", ""))
            click_url = self.anchors[-1]["url"] if self.anchors else None
            self.images.append({
                "source": src[:2048],
                "alt": alt[:500],
                "click_url": click_url,
                "remote": src.lower().startswith(("http://", "https://")),
                "inline": src.lower().startswith("cid:"),
            })
            if self.anchors:
                self.anchors[-1]["has_image"] = True
            if click_url:
                self.links.append({"text": alt or "linked image", "url": click_url, "source": "linked_image"})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in self.SKIP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag == "a" and self.anchors:
            anchor = self.anchors.pop()
            url = anchor.get("url")
            text = normalize_space("".join(anchor.get("text") or []))
            if url and not anchor.get("has_image"):
                self.links.append({"text": text or url, "url": url, "source": "anchor"})
                self.parts.append(f" ({url})")
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        self.parts.append(data)
        if self.anchors:
            self.anchors[-1]["text"].append(data)

    def result(self) -> tuple[str, list[dict[str, str]], list[dict[str, Any]]]:
        return normalize_space("".join(self.parts)), dedupe_links(self.links), self.images


def html_to_text(value: str) -> tuple[str, list[dict[str, str]], list[dict[str, Any]], list[str]]:
    parser = VisibleHTMLParser()
    warnings: list[str] = []
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:
        warnings.append(f"html_parse_failed: {exc}")
    text, links, images = parser.result()
    return text, links, images, warnings


def decode_part(part: Message) -> str:
    try:
        value = part.get_content()
        if isinstance(value, str):
            return value
    except Exception:
        pass
    payload = part.get_payload(decode=True)
    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def text_score(text: str) -> int:
    score = len(re.sub(r"\s+", "", text))
    if any(pattern.search(text) for pattern in PLACEHOLDERS):
        score -= 500
    return score


def is_body_candidate(part: Message) -> bool:
    return (
        not part.is_multipart()
        and part.get_content_disposition() != "attachment"
        and not part.get_filename()
        and part.get_content_type() in {"text/plain", "text/html"}
    )


def sanitize_filename(name: str | None, fallback: str) -> str:
    name = Path(name or fallback).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name[:120] or fallback


def secure_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp_name, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def secure_write_text(path: Path, text: str) -> None:
    secure_write_bytes(path, text.encode("utf-8", errors="replace"))


def detect_type(data: bytes, filename: str, declared: str) -> str:
    signatures = (
        (b"%PDF-", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
    )
    for prefix, content_type in signatures:
        if data.startswith(prefix):
            return content_type
    if data.startswith(b"PK\x03\x04"):
        return {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }.get(Path(filename).suffix.lower(), "application/zip")
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or declared or "application/octet-stream"


def xml_text(data: bytes) -> str:
    if len(data) > MAX_XML_BYTES:
        raise ValueError("office_xml_too_large")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ""
    return normalize_space("\n".join(
        node.text for node in root.iter() if node.text and node.text.strip()
    ))


def validate_office_archive(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_OFFICE_MEMBERS:
        raise ValueError("office_archive_member_limit")
    total_uncompressed = sum(member.file_size for member in members)
    total_compressed = sum(max(member.compress_size, 1) for member in members)
    if total_uncompressed > MAX_OFFICE_UNCOMPRESSED_BYTES:
        raise ValueError("office_archive_size_limit")
    if total_uncompressed / total_compressed > MAX_OFFICE_COMPRESSION_RATIO:
        raise ValueError("office_archive_compression_ratio_limit")


def extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        validate_office_archive(archive)
        parts = [
            xml_text(archive.read(name))
            for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml")
            if name in archive.namelist()
        ]
    return normalize_space("\n\n".join(parts))


def extract_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        validate_office_archive(archive)
        names = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        slides = [xml_text(archive.read(name)) for name in names]
    return normalize_space("\n\n".join(
        f"Slide {index}\n{text}" for index, text in enumerate(slides, 1)
    ))


def extract_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        validate_office_archive(archive)
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            try:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in root.iter():
                    if item.tag.endswith("}si"):
                        shared.append("".join(
                            node.text or "" for node in item.iter() if node.tag.endswith("}t")
                        ))
            except ET.ParseError:
                pass
        sheet_names = sorted(
            name for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        output: list[str] = []
        for sheet_index, name in enumerate(sheet_names, 1):
            output.append(f"Sheet {sheet_index}")
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                continue
            for cell in root.iter():
                if not cell.tag.endswith("}c"):
                    continue
                ref = cell.attrib.get("r", "")
                kind = cell.attrib.get("t")
                value = next((node.text or "" for node in cell if node.tag.endswith("}v")), "")
                if kind == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
                if value:
                    output.append(f"{ref}: {value}")
    return normalize_space("\n".join(output))


def extract_pdf(path: Path, timeout: int = 30) -> tuple[str, str | None]:
    executable = shutil.which("pdftotext")
    if not executable:
        return "", "pdftotext_unavailable"
    completed = subprocess.run(
        [executable, "-layout", str(path), "-"],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace")[:200]
        return "", f"pdftotext_failed: {error}"
    return completed.stdout.decode("utf-8", errors="replace"), None


def extract_attachment_text(
    path: Path,
    filename: str,
    content_type: str,
    max_chars: int,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    extension = Path(filename).suffix.lower()
    if extension in BLOCKED_EXTENSIONS:
        return "", ["blocked_executable_type"]
    if content_type.startswith("image/") or content_type in {
        "application/pkcs7-signature",
        "application/x-pkcs7-signature",
    }:
        return "", []
    try:
        if extension in TEXT_EXTENSIONS or content_type.startswith("text/"):
            text = path.read_bytes().decode("utf-8", errors="replace")
        elif extension == ".pdf" or content_type == "application/pdf":
            text, warning = extract_pdf(path)
            if warning:
                warnings.append(warning)
        elif extension == ".docx":
            text = extract_docx(path)
        elif extension == ".xlsx":
            text = extract_xlsx(path)
        elif extension == ".pptx":
            text = extract_pptx(path)
        else:
            return "", ["unsupported_attachment_type"]
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, subprocess.SubprocessError) as exc:
        return "", [f"attachment_parse_failed: {exc}"]
    text = normalize_space(text)
    if len(text) > max_chars:
        text = text[:max_chars]
        warnings.append("attachment_text_truncated")
    if not text and extension == ".pdf":
        warnings.append("pdf_may_require_ocr")
    return text, warnings


def extract_message(
    raw_message: bytes,
    attachment_dir: Path,
    *,
    max_body_chars: int = 12000,
    max_attachment_count: int = 10,
    max_inline_image_count: int = 5,
    max_single_attachment_bytes: int = 15 * 1024 * 1024,
    max_total_attachment_bytes: int = 30 * 1024 * 1024,
    max_attachment_text_chars: int = 30000,
) -> dict[str, Any]:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    plain_parts: list[str] = []
    html_parts: list[str] = []
    links: list[dict[str, str]] = []
    images: list[dict[str, Any]] = []
    warnings: list[str] = []

    for part in message.walk():
        if not is_body_candidate(part):
            continue
        value = decode_part(part)
        if part.get_content_type() == "text/plain":
            plain_parts.append(value)
        else:
            html_parts.append(value)

    plain_text = normalize_space("\n\n".join(plain_parts))
    html_text_parts: list[str] = []
    for value in html_parts:
        text, html_links, html_images, html_warnings = html_to_text(value)
        html_text_parts.append(text)
        links.extend(html_links)
        images.extend(html_images)
        warnings.extend(html_warnings)
    html_text = normalize_space("\n\n".join(html_text_parts))
    links.extend(
        {"text": match.group(0), "url": match.group(0), "source": "plain_text"}
        for match in URL_RE.finditer(plain_text)
    )

    plain_score = text_score(plain_text)
    html_score = text_score(html_text)
    if plain_text and plain_score >= 40 and plain_score >= int(html_score * 0.35):
        body, text_source = plain_text, "text/plain"
    elif html_text:
        body, text_source = html_text, "text/html"
    elif plain_text:
        body, text_source = plain_text, "text/plain"
    else:
        body, text_source = "", "none"

    body_original_chars = len(body)
    body_truncated = body_original_chars > max_body_chars
    if body_truncated:
        body = body[:max_body_chars]
        warnings.append("body_text_truncated")

    attachment_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        attachment_dir.chmod(0o700)
    except OSError:
        pass
    attachments: list[dict[str, Any]] = []
    attachment_sections: list[str] = []
    saved_attachment_bytes = 0
    saved_attachment_count = 0
    saved_inline_image_count = 0
    referenced_content_ids = {
        str(image.get("source", ""))[4:].lower()
        for image in images
        if str(image.get("source", "")).lower().startswith("cid:")
    }

    for index, part in enumerate(message.walk(), 1):
        if part.is_multipart() or is_body_candidate(part):
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        declared_type = part.get_content_type()
        if not filename and disposition not in {"attachment", "inline"} and not declared_type.startswith("image/"):
            continue
        payload = part.get_payload(decode=True) or b""
        guessed_extension = mimetypes.guess_extension(declared_type) or ".bin"
        safe_name = sanitize_filename(filename, f"part-{index}{guessed_extension}")
        detected_type = detect_type(payload, safe_name, declared_type)
        digest = hashlib.sha256(payload).hexdigest()
        extension = Path(safe_name).suffix.lower()
        stored_path = attachment_dir / f"{digest[:20]}{extension}"
        status = "metadata_only"
        item_warnings: list[str] = []
        text_path: str | None = None

        content_id = (part.get("Content-ID") or "").strip("<>") or None
        inline = disposition == "inline" or bool(
            content_id and content_id.lower() in referenced_content_ids
        )
        is_inline_image = inline and detected_type.startswith("image/")
        is_signature = detected_type in {
            "application/pkcs7-signature",
            "application/x-pkcs7-signature",
        }

        if is_signature:
            status = "metadata_only_signature"
        elif is_inline_image and saved_inline_image_count >= max_inline_image_count:
            status = "skipped_inline_image_limit"
        elif not is_inline_image and saved_attachment_count >= max_attachment_count:
            status = "skipped_count_limit"
        elif len(payload) > max_single_attachment_bytes:
            status = "skipped_size_limit"
        elif saved_attachment_bytes + len(payload) > max_total_attachment_bytes:
            status = "skipped_total_size_limit"
        elif extension in BLOCKED_EXTENSIONS:
            status = "blocked_executable_type"
        else:
            secure_write_bytes(stored_path, payload)
            saved_attachment_bytes += len(payload)
            if is_inline_image:
                saved_inline_image_count += 1
            else:
                saved_attachment_count += 1
            status = "saved"
            extracted_text, item_warnings = extract_attachment_text(
                stored_path, safe_name, detected_type, max_attachment_text_chars
            )
            if extracted_text:
                extracted_path = stored_path.with_suffix(stored_path.suffix + ".txt")
                secure_write_text(extracted_path, extracted_text)
                text_path = str(extracted_path)
                status = "extracted"
                attachment_sections.append(f"附件 {safe_name}:\n{extracted_text}")

        attachment = {
            "part_index": index,
            "filename": safe_name,
            "content_type_declared": declared_type,
            "content_type_detected": detected_type,
            "size": len(payload),
            "sha256": digest,
            "inline": inline,
            "content_id": content_id,
            "status": status,
            "stored_path": str(stored_path) if stored_path.exists() else None,
            "text_path": text_path,
            "parse_warnings": item_warnings,
        }
        attachments.append(attachment)
        if detected_type.startswith("image/"):
            attachment_image = {
                "source": f"cid:{attachment['content_id']}" if attachment["content_id"] else safe_name,
                "alt": safe_name,
                "click_url": None,
                "remote": False,
                "inline": inline,
                "attachment_sha256": digest,
            }
            matching_image = next(
                (
                    image for image in images
                    if attachment["content_id"]
                    and image.get("source", "").lower() == f"cid:{attachment['content_id']}".lower()
                ),
                None,
            )
            if matching_image:
                matching_image.update({
                    "inline": inline,
                    "attachment_sha256": digest,
                    "filename": safe_name,
                })
            else:
                images.append(attachment_image)

    has_images = bool(images)
    body_without_urls = normalize_space(URL_RE.sub("", body)).strip("()[]{}<> -")
    image_only = has_images and not re.search(r"\w", body_without_urls, re.UNICODE)
    if image_only:
        content_kind = "image_link" if any(image.get("click_url") for image in images) else "image_only"
    elif html_parts and plain_parts:
        content_kind = "mixed"
    elif html_parts:
        content_kind = "html"
    elif plain_parts:
        content_kind = "plain"
    elif attachments:
        content_kind = "attachment_only"
    else:
        content_kind = "empty"

    attachment_text = normalize_space("\n\n".join(attachment_sections))
    if len(attachment_text) > max_attachment_text_chars:
        attachment_text = attachment_text[:max_attachment_text_chars]
        warnings.append("combined_attachment_text_truncated")

    return {
        "content_kind": content_kind,
        "text_source": text_source,
        "text": body,
        "body_original_chars": body_original_chars,
        "body_extracted_chars": len(body),
        "body_truncated": body_truncated,
        "links": dedupe_links(links),
        "images": images,
        "attachments": attachments,
        "attachment_text": attachment_text,
        "has_inline_images": any(image.get("inline") for image in images),
        "image_only": image_only,
        "parse_warnings": warnings,
    }


def write_manifest(path: Path, result: dict[str, Any]) -> None:
    manifest = {
        key: value for key, value in result.items()
        if key not in {"text", "attachment_text"}
    }
    secure_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2))
