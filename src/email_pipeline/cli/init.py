"""Initialize a self-contained email-pipeline instance directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from email_pipeline.paths import INSTANCE_ROOT_ENV, code_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the current directory as an email-pipeline instance.")
    parser.parse_args()
    root = Path.cwd().resolve()
    state = root
    if (state / "instance.toml").exists():
        raise SystemExit(f"already initialized: {root}")
    repo = code_root()
    config = root / "config"
    config.mkdir(parents=True, mode=0o700)
    (state / "instance.toml").write_text("version = 1\n", encoding="utf-8")
    shutil.copy2(repo / "daily-mail-pipeline.toml.example", config / "daily-mail-pipeline.toml")
    for stage in ("summarizer", "aggregator"):
        shutil.copy2(repo / "opencode" / stage / "system-prompt.txt", config / f"{stage}-system-prompt.txt")
    for path in (state / "instance.toml", config / "daily-mail-pipeline.toml", config / "summarizer-system-prompt.txt", config / "aggregator-system-prompt.txt"):
        path.chmod(0o600)
    print(f"Initialized email-pipeline instance: {root}")
    print(f"Run commands from this directory, or set {INSTANCE_ROOT_ENV}={root}")
    return 0
