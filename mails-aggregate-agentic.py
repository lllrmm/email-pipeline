#!/usr/bin/env python3
"""Aggregate fixed per-email agent results for one day with OpenCode."""

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
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.daily_schema import validate_daily_summary  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402

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


def parse_events(stdout: str) -> tuple[str | None, list[str], dict[str, Any] | None]:
    session_id = None
    tools: list[str] = []
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
            tool_name = item.get("tool") or item.get("toolName") or item.get("name")
            item_type = str(item.get("type") or "").lower()
            if isinstance(tool_name, str) and "tool" in item_type and tool_name.startswith("mail_"):
                tools.append(tool_name)
            if isinstance(item.get("text"), str):
                candidates.append(item["text"])
    for text in reversed(candidates):
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and "overview" in result:
            return session_id, list(dict.fromkeys(tools)), result
    return session_id, list(dict.fromkeys(tools)), None


def validate_pipeline_inputs(workdir: Path, pipeline_ids: list[str]) -> None:
    for pipeline_id in pipeline_ids:
        if len(pipeline_id) != 64 or any(char not in "0123456789abcdef" for char in pipeline_id):
            raise RuntimeError(f"invalid pipeline id: {pipeline_id}")
        email_dir = workdir / "emails" / pipeline_id
        request_path = email_dir / "request.json"
        summary_path = email_dir / "summary.json"
        if not request_path.is_file():
            raise RuntimeError(f"request.json missing: {pipeline_id}")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if request.get("pipeline_id") != pipeline_id:
            raise RuntimeError(f"request.json does not match pipeline id: {pipeline_id}")
        database_path = request.get("index_database")
        if not database_path:
            raise RuntimeError(f"index database missing from request: {pipeline_id}")
        identity = MailIdentityIndex(Path(database_path)).lookup_pipeline_id(pipeline_id)
        if identity is None:
            raise RuntimeError(f"pipeline id is absent from database: {pipeline_id}")
        if identity.get("summarized") is not True:
            raise RuntimeError(f"pipeline id is not summarized: {pipeline_id}")
        if not summary_path.is_file():
            raise RuntimeError(f"summary.json missing: {pipeline_id}")
        artifact = json.loads(summary_path.read_text(encoding="utf-8"))
        if artifact.get("pipeline_id") != pipeline_id or not isinstance(artifact.get("analysis"), dict):
            raise RuntimeError(f"summary.json does not match pipeline id: {pipeline_id}")


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-id-list", required=True, nargs="*")
    parser.add_argument("--agent-workdir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    workdir = args.agent_workdir.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    if not workdir.is_dir():
        raise RuntimeError(f"agent work directory does not exist: {workdir}")
    if output_path != workdir and workdir not in output_path.parents:
        raise RuntimeError("output path must stay inside the agent work directory")
    if output_path.name != "aggregation.json" or output_path.parent != workdir:
        raise RuntimeError("aggregate output must be <agent-workdir>/aggregation.json")
    pipeline_ids = list(dict.fromkeys(args.pipeline_id_list))
    validate_pipeline_inputs(workdir, pipeline_ids)
    secure_write(workdir / "pipeline-id-list.json", json.dumps({
        "date": workdir.name,
        "pipeline_ids": pipeline_ids,
    }, ensure_ascii=False, indent=2))
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
        "--dir", str(workdir),
        "--title", f"mail-daily:{workdir.name}",
        "Read pipeline-id-list.json and each emails/<pipeline_id>/summary.json, then return the required compact daily JSON digest.",
    ]
    completed = subprocess.run(
        command, stdin=subprocess.DEVNULL, text=True, capture_output=True,
        timeout=timeout, env=env, check=False,
    )
    session_id, tools_used, result = parse_events(completed.stdout)
    if completed.returncode != 0 or result is None:
        raise RuntimeError(f"daily aggregation failed: {completed.stderr[-1000:]} {completed.stdout[-1000:]}")
    validate_daily_summary(result)
    output = {
        "schema_version": 3,
        "artifact_type": "mail_daily_summary",
        "processor": {
            "processor": "opencode",
            "session_id": session_id,
            "model": model,
            "tools_used": tools_used,
        },
        "daily_summary": result,
    }
    secure_write(output_path, json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "summary_path": str(output_path), "session_id": session_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
