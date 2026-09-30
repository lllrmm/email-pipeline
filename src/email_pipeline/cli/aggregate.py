#!/usr/bin/env python3
"""Aggregate fixed per-email agent results for one day with OpenCode."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from email_pipeline.aggregator.aggregate import run
from email_pipeline.config import default_config_path

DEFAULT_CONFIG = default_config_path()


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-id-list", required=True, nargs="*")
    parser.add_argument("--agent-workdir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    result = run(
        args.pipeline_id_list,
        args.agent_workdir,
        args.output_dir,
        args.config,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
