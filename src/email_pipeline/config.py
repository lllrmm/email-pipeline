"""TOML configuration loading for the email pipeline."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "daily-mail-pipeline.toml"


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError("config must be a TOML table")
    return value
