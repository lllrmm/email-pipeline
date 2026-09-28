#!/usr/bin/env python3
"""Register one RFC Message-ID and its IMAP UID transport location."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.mail_identity import MailIdentityIndex, get_or_create_salt, make_pipeline_id  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR / "daily-mail-pipeline.yaml"


def register_mail(
    rfc_message_id: str,
    folder: str,
    uidvalidity: int,
    uid: int,
    *,
    account: str = "outlook",
    salt: bytes,
    database_path: Path,
) -> dict[str, str]:
    rfc_message_id = rfc_message_id.strip()
    folder = folder.strip()
    if not rfc_message_id:
        raise RuntimeError("RFC Message-ID is required")
    if not folder:
        raise RuntimeError("folder is required")
    pipeline_id = make_pipeline_id(salt, rfc_message_id)
    index = MailIdentityIndex(database_path)
    created = index.register_identity(
        pipeline_id=pipeline_id,
        rfc_message_id=rfc_message_id,
    )
    index.record_imap_location(
        pipeline_id=pipeline_id,
        account=account,
        folder=folder,
        uidvalidity=uidvalidity,
        uid=uid,
        observed_date=None,
    )
    return {
        "status": "registered" if created else "already_registered",
        "pipeline_id": pipeline_id,
    }


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Register one RFC Message-ID and IMAP UID location.")
    parser.add_argument("--rfc-message-id", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--uidvalidity", required=True, type=int)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.expanduser().resolve().read_text(encoding="utf-8")) or {}
    identity = config.get("identity") or {}
    database_path = Path(identity.get("database_path") or (Path.home() / ".hermes" / "email" / "mail-index.sqlite3")).expanduser().resolve()
    salt_path = Path(identity.get("salt_path") or (Path.home() / ".hermes" / "email" / "pipeline-id-salt")).expanduser().resolve()
    result = register_mail(
        args.rfc_message_id,
        args.folder,
        args.uidvalidity,
        args.uid,
        account=str(identity.get("account") or "outlook"),
        salt=get_or_create_salt(salt_path),
        database_path=database_path,
    )
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
