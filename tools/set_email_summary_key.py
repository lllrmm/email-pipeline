#!/usr/bin/env python3
"""Store the dedicated email-summary API key without echoing it."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def configure_key(config_path: Path, env_path: Path, env_name: str, key: str) -> None:
    key = key.strip()
    if not key or "\n" in key or "\r" in key:
        raise ValueError("invalid API key")

    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    env_lines = [
        line for line in env_text.splitlines()
        if not line.startswith(f"{env_name}=")
    ]
    env_lines.append(f"{env_name}={key}")
    atomic_write(env_path, "\n".join(env_lines).rstrip() + "\n")

    config_text = config_path.read_text(encoding="utf-8")
    config_text, count = re.subn(
        r"(?m)^(?P<indent>[ \t]*)api_key_env:[^\r\n]*$",
        rf'\g<indent>api_key_env: "{env_name}"',
        config_text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("api_key_env entry not found in config")
    config_text = re.sub(
        r"(?m)^(?P<indent>[ \t]*)api_key:[^\r\n]*$",
        r'\g<indent>api_key: ""',
        config_text,
        count=1,
    )
    atomic_write(config_path, config_text.rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--env-name", default="EMAIL_SUMMARY_DEEPSEEK_API_KEY")
    args = parser.parse_args()

    key = sys.stdin.readline()
    configure_key(args.config.expanduser(), args.env.expanduser(), args.env_name, key)
    print(f"Configured dedicated email-summary credential: {args.env_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
