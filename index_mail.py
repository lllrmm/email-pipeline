#!/usr/bin/env python3
"""Register one RFC Message-ID and return its stable pipeline ID."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.mail_identity import (  # noqa: E402
    MailIdentityIndex,
    get_or_create_salt,
    make_pipeline_id,
)


def register_rfc_message_id(
    rfc_message_id: str,
    *,
    salt: bytes,
    database_path: Path,
) -> dict[str, str]:
    normalized = rfc_message_id.strip()
    if not normalized:
        raise RuntimeError("RFC Message-ID is required")
    pipeline_id = make_pipeline_id(salt, normalized)
    created = MailIdentityIndex(database_path).register_identity(
        pipeline_id=pipeline_id,
        rfc_message_id=normalized,
    )
    return {
        "status": "registered" if created else "already_registered",
        "pipeline_id": pipeline_id,
    }


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Register one RFC Message-ID in SQLite.")
    parser.add_argument("--rfc-message-id", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--salt-path", required=True, type=Path)
    args = parser.parse_args()

    result = register_rfc_message_id(
        args.rfc_message_id,
        salt=get_or_create_salt(args.salt_path.expanduser().resolve()),
        database_path=args.database.expanduser().resolve(),
    )
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
