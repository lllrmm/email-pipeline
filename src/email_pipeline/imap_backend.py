"""Read-only IMAPClient connection and fetch helpers."""

from __future__ import annotations

import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from .paths import token_refresh_path

IMAP_SOCKET_TIMEOUT_SECONDS = 60
TOKEN_READ_ONLY_FLAG = "--read-only"

def imap_config(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("imap") or {}
    return {
        "host": str(value.get("host") or "outlook.office365.com"),
        "port": int(value.get("port") or 993),
        "ssl": bool(value.get("ssl", True)),
        "username": str(value.get("username") or ""),
        "token_command": str(value.get("token_command") or f"{token_refresh_path()} {TOKEN_READ_ONLY_FLAG}"),
    }


def access_token(command: str) -> str:
    argv = shlex.split(command) if isinstance(command, str) else [str(part) for part in command]
    completed = subprocess.run(argv, text=True, capture_output=True, timeout=60, check=False)
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise RuntimeError(f"IMAP OAuth token command failed: {completed.stderr.strip()[:500]}")
    return token


@contextmanager
def connect_imap(config: dict[str, Any]) -> Iterator[Any]:
    from imapclient import IMAPClient
    cfg = imap_config(config)
    if not cfg["username"]:
        raise RuntimeError("imap.username is required")
    client = IMAPClient(
        cfg["host"],
        port=cfg["port"],
        ssl=cfg["ssl"],
        use_uid=True,
        timeout=IMAP_SOCKET_TIMEOUT_SECONDS,
    )
    try:
        client.oauth2_login(cfg["username"], access_token(cfg["token_command"]))
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass


def response_bytes(value: dict[bytes, Any]) -> bytes:
    candidates = [item for item in value.values() if isinstance(item, bytes)]
    if not candidates:
        raise RuntimeError("IMAP FETCH response has no message bytes")
    return max(candidates, key=len)


def fetch_raw(config: dict[str, Any], folder: str, uidvalidity: int, uid: int) -> bytes:
    with connect_imap(config) as client:
        selected = client.select_folder(folder, readonly=True)
        current = int(selected[b"UIDVALIDITY"])
        if current != int(uidvalidity):
            raise RuntimeError(f"UIDVALIDITY changed for {folder}: {uidvalidity} -> {current}")
        fetched = client.fetch([int(uid)], [b"BODY.PEEK[]"])
        if int(uid) not in fetched:
            raise RuntimeError(f"IMAP UID not found: {folder}/{uid}")
        return response_bytes(fetched[int(uid)])
