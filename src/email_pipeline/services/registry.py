#!/usr/bin/env python3
"""Register one RFC Message-ID and its IMAP UID transport location."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from email_pipeline.config import default_config_path, load_config  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex, get_or_create_salt, make_pipeline_id, normalize_rfc_message_id  # noqa: E402
from email_pipeline.program_time import configure_program_timezone  # noqa: E402
from email_pipeline.paths import database_path as default_database_path, salt_path as default_salt_path  # noqa: E402

DEFAULT_CONFIG = default_config_path()


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
    try:
        rfc_message_id = normalize_rfc_message_id(rfc_message_id)
    except ValueError as exc:
        raise RuntimeError("RFC Message-ID is required") from exc
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

    config = load_config(args.config.expanduser().resolve())
    configure_program_timezone(str(config.get("timezone") or "UTC"))
    identity = config.get("identity") or {}
    database_path = Path(identity.get("database_path") or default_database_path()).expanduser().resolve()
    salt_path = Path(identity.get("salt_path") or default_salt_path()).expanduser().resolve()
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
