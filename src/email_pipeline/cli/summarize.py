#!/usr/bin/env python3
"""Analyze one indexed email with an OpenCode agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from email_pipeline.config import default_config_path
from email_pipeline.summarizer.summarize import run

DEFAULT_CONFIG = default_config_path()


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Analyze one indexed email with an OpenCode agent.")
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--mail-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--title")
    args = parser.parse_args()
    result = run(
        args.pipeline_id,
        args.mail_dir,
        args.output,
        args.config.expanduser().resolve(),
        title=args.title,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
