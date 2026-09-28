#!/usr/bin/env python3
"""Register one scanned email envelope in the stable mail index."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.mail_identity import (  # noqa: E402
    MailIdentityIndex,
    get_or_create_salt,
    make_pipeline_id,
)
from email_pipeline.mime_extract import secure_write_text  # noqa: E402


def envelope_address_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        rendered = [envelope_address_text(item) for item in value]
        return ", ".join(item for item in rendered if item) or None
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        address = str(value.get("addr") or value.get("address") or value.get("email") or "").strip()
        if name and address:
            return f"{name} <{address}>"
        return address or name or None
    return str(value).strip() or None


def register_mail(
    folder: str,
    envelope: dict[str, Any],
    day_dir: Path,
    *,
    account: str,
    salt: bytes,
    database_path: Path,
    observed_date: str,
) -> dict[str, Any]:
    """Register one envelope and create its stable workspace request."""
    himalaya_id = str(envelope.get("id") or "").strip()
    if not himalaya_id:
        raise RuntimeError(f"Himalaya message ID missing for envelope in {folder}")
    rfc_message_id = str(envelope.get("message-id") or "").strip()
    if not rfc_message_id:
        raise RuntimeError(f"RFC Message-ID missing for envelope {folder}/{himalaya_id}")

    pipeline_id = make_pipeline_id(salt, rfc_message_id)
    index = MailIdentityIndex(database_path)
    index.record(
        pipeline_id=pipeline_id,
        rfc_message_id=rfc_message_id,
        identity_source="rfc_message_id",
        account=account,
        folder=folder,
        himalaya_id=himalaya_id,
        observed_date=observed_date,
        sent_at=str(envelope.get("date") or "").strip() or None,
        subject=str(envelope.get("subject") or "").strip() or None,
        sender=envelope_address_text(envelope.get("from")),
    )

    mail_dir = day_dir / "emails" / pipeline_id
    mail_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    index.record_workspace(pipeline_id, observed_date, mail_dir)
    request = {
        "pipeline_id": pipeline_id,
        "rfc_message_id": rfc_message_id,
        "identity_source": "rfc_message_id",
        "subject": envelope.get("subject"),
        "date": envelope.get("date"),
        "index_database": str(database_path),
    }
    secure_write_text(mail_dir / "request.json", json.dumps(request, ensure_ascii=False, indent=2))
    return {
        "pipeline_id": pipeline_id,
        "rfc_message_id": rfc_message_id,
        "observed_date": observed_date,
        "mail_dir": str(mail_dir),
    }


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Register one mail envelope in SQLite.")
    parser.add_argument("--folder", required=True)
    parser.add_argument("--envelope-json", required=True)
    parser.add_argument("--day-dir", required=True, type=Path)
    parser.add_argument("--observed-date", required=True)
    parser.add_argument("--account", default="outlook")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--salt-path", required=True, type=Path)
    args = parser.parse_args()

    envelope = json.loads(args.envelope_json)
    if not isinstance(envelope, dict):
        raise RuntimeError("envelope JSON must be an object")
    result = register_mail(
        args.folder,
        envelope,
        args.day_dir.expanduser().resolve(),
        account=args.account,
        salt=get_or_create_salt(args.salt_path.expanduser().resolve()),
        database_path=args.database.expanduser().resolve(),
        observed_date=args.observed_date,
    )
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
