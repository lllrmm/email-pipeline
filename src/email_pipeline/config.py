"""TOML configuration loading for the email pipeline."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "daily-mail-pipeline.toml"


def load_config(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    with path.open("rb") as handle:
        value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError("config must be a TOML table")
    for stage_name in ("summarizer", "aggregator"):
        stage = value.get(stage_name) or {}
        prompt_file = stage.get("system_prompt_file")
        inline_prompt = stage.get("system_prompt")
        if prompt_file is None:
            continue
        if inline_prompt is not None:
            raise RuntimeError(f"configure {stage_name}.system_prompt_file or system_prompt, not both")
        if not isinstance(prompt_file, str) or not prompt_file.strip():
            raise RuntimeError(f"{stage_name}.system_prompt_file must be a non-empty string")
        prompt_path = (path.parent / prompt_file).resolve()
        if prompt_path.parent != path.parent:
            raise RuntimeError(f"{stage_name}.system_prompt_file must stay in the config directory")
        prompt = prompt_path.read_text(encoding="utf-8").rstrip("\n")
        if not prompt.strip():
            raise RuntimeError(f"{stage_name} system prompt file is empty")
        stage["system_prompt"] = prompt
        value[stage_name] = stage
    return value
