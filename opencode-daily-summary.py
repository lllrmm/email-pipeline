#!/usr/bin/env python3
"""Run an OpenCode agent to aggregate per-email analyses for one day."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "daily-mail-pipeline.yaml"


def secure_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def load_config(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise RuntimeError("config must be a YAML mapping")
    return value


def resolve_key(cfg: dict[str, Any]) -> str:
    env_name = str((cfg.get("api") or {}).get("api_key_env") or "EMAIL_SUMMARY_DEEPSEEK_API_KEY")
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    for line in (Path.home() / ".hermes" / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"{env_name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"credential not found: {env_name}")


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def parse_events(stdout: str) -> tuple[str | None, dict[str, Any] | None]:
    session_id = None
    candidates: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for item in walk(event):
            for key in ("sessionID", "sessionId", "session_id"):
                if isinstance(item.get(key), str):
                    session_id = item[key]
            if isinstance(item.get("text"), str):
                candidates.append(item["text"])
    for text in reversed(candidates):
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and "overview" in result:
            return session_id, result
    return session_id, None


def source_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {
            "folder": item.get("folder"),
            "id": item.get("id"),
            "subject": item.get("subject"),
        }
        for item in value
        if isinstance(item, dict)
    ]


def normalize_daily_summary(value: dict[str, Any]) -> dict[str, Any]:
    def rows(name: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        raw = value.get(name)
        if not isinstance(raw, list):
            return []
        output = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            row = {field: item.get(field) for field in fields}
            row["source_messages"] = source_messages(item.get("source_messages"))
            output.append(row)
        return output

    warnings = value.get("warnings")
    return {
        "date": value.get("date"),
        "overview": str(value.get("overview") or ""),
        "urgent_items": rows("urgent_items", ("title", "reason")),
        "events": rows("events", ("title", "start", "end", "timezone", "location", "confidence")),
        "deadlines": rows("deadlines", ("what", "date", "time", "timezone", "confidence")),
        "actions": rows("actions", ("what", "due", "priority")),
        "warnings": [str(item) for item in warnings] if isinstance(warnings, list) else [],
        "messages_total": int(value.get("messages_total") or 0),
        "messages_requiring_review": int(value.get("messages_requiring_review") or 0),
    }


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    cfg = load_config(config_path)
    oc = cfg.get("opencode") or {}
    executable = str(oc.get("executable") or (Path.home() / ".opencode" / "bin" / "opencode"))
    model = str(oc.get("model") or "deepseek/deepseek-flash")
    timeout = int(oc.get("timeout_seconds") or 600)
    runtime_root = Path(oc.get("runtime_root") or (Path.home() / ".hermes" / "opencode-email-runtime")).expanduser().resolve()
    runtime_home = runtime_root / "home"
    runtime_config = runtime_root / "config"
    runtime_data = runtime_root / "data"
    runtime_cache = runtime_root / "cache"
    runtime_state = runtime_root / "state"
    for path in (runtime_home, runtime_config / "opencode", runtime_data, runtime_cache, runtime_state):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = resolve_key(cfg)
    env["HOME"] = str(runtime_home)
    env["XDG_CONFIG_HOME"] = str(runtime_config)
    env["XDG_DATA_HOME"] = str(runtime_data)
    env["XDG_CACHE_HOME"] = str(runtime_cache)
    env["XDG_STATE_HOME"] = str(runtime_state)
    env["OPENCODE_CONFIG_DIR"] = str(runtime_config / "opencode")

    command = [
        executable, "run", "--format", "json",
        "--agent", "mail-daily-aggregator",
        "--model", model,
        "--dir", str(input_path.parent),
        "--title", f"mail-daily:{input_path.parent.name}",
        "Read individual-results.json and return the required compact daily JSON digest.",
    ]
    completed = subprocess.run(
        command, stdin=subprocess.DEVNULL, text=True, capture_output=True,
        timeout=timeout, env=env, check=False,
    )
    session_id, result = parse_events(completed.stdout)
    if completed.returncode != 0 or result is None:
        raise RuntimeError(f"daily aggregation failed: {completed.stderr[-1000:]} {completed.stdout[-1000:]}")
    output = {
        "schema_version": 3,
        "artifact_type": "mail_daily_summary",
        "processor": {
            "processor": "opencode",
            "session_id": session_id,
            "model": model,
        },
        "daily_summary": normalize_daily_summary(result),
    }
    secure_write(output_path, json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "summary_path": str(output_path), "session_id": session_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
