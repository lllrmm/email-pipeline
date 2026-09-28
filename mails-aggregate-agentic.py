#!/usr/bin/env python3
"""Aggregate fixed per-email agent results for one day with OpenCode."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
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


def generated_at_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def aggregation_filename(generated_at: str) -> str:
    timestamp = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    timestamp = timestamp.astimezone(dt.timezone.utc)
    return f"aggregation-{timestamp.strftime('%Y%m%dT%H%M%S.%fZ')}.json"


def email_time_bounds(values: list[str]) -> tuple[str | None, str | None]:
    parsed: list[dt.datetime] = []
    for value in values:
        try:
            timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if timestamp.tzinfo is None:
            continue
        parsed.append(timestamp.astimezone(dt.timezone.utc))
    if not parsed:
        return None, None
    return (
        min(parsed).isoformat().replace("+00:00", "Z"),
        max(parsed).isoformat().replace("+00:00", "Z"),
    )


def build_aggregation_artifact(
    result: dict[str, Any],
    pipeline_ids: list[str],
    *,
    earliest_email_at: str | None,
    latest_email_at: str | None,
    session_id: str | None,
    model: str,
    tools_used: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "artifact_type": "mail_daily_summary",
        "generated_at": generated_at_utc(),
        "included_pipeline_ids": list(pipeline_ids),
        "earliest_email_at": earliest_email_at,
        "latest_email_at": latest_email_at,
        "processor": {
            "processor": "opencode",
            "session_id": session_id,
            "model": model,
            "tools_used": tools_used,
        },
        "daily_summary": result,
    }


def secure_write(path: Path, text: str) -> None:
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


def parse_json_object(text: str) -> dict[str, Any] | None:
    value = text.strip()
    if value.startswith("```"):
        first_newline = value.find("\n")
        value = value[first_newline + 1:] if first_newline >= 0 else value
        if value.rstrip().endswith("```"):
            value = value.rstrip()[:-3].rstrip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(value[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


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
        result = parse_json_object(text)
        if isinstance(result, dict) and "overview" in result:
            return session_id, list(dict.fromkeys(tools)), result
    return session_id, list(dict.fromkeys(tools)), None


def validate_pipeline_inputs(workdir: Path, pipeline_ids: list[str]) -> list[str]:
    sent_times: list[str] = []
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
        metadata = identity.get("metadata") or {}
        sent_at = metadata.get("sent_at") or request.get("date")
        if isinstance(sent_at, str) and sent_at.strip():
            sent_times.append(sent_at.strip())
        if not summary_path.is_file():
            raise RuntimeError(f"summary.json missing: {pipeline_id}")
        artifact = json.loads(summary_path.read_text(encoding="utf-8"))
        if artifact.get("pipeline_id") != pipeline_id or not isinstance(artifact.get("analysis"), dict):
            raise RuntimeError(f"summary.json does not match pipeline id: {pipeline_id}")
    return sent_times


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline-id-list", required=True, nargs="*")
    parser.add_argument("--agent-workdir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    workdir = args.agent_workdir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    config_path = args.config.expanduser().resolve()
    if not workdir.is_dir():
        raise RuntimeError(f"agent work directory does not exist: {workdir}")
    expected_output_dir = (workdir / "aggregation").resolve()
    if output_dir != expected_output_dir:
        raise RuntimeError("aggregate output directory must be <agent-workdir>/aggregation")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    pipeline_ids = list(dict.fromkeys(args.pipeline_id_list))
    sent_times = validate_pipeline_inputs(workdir, pipeline_ids)
    earliest_email_at, latest_email_at = email_time_bounds(sent_times)
    cfg = load_config(config_path)
    oc = cfg.get("opencode") or {}
    executable = str(oc.get("executable") or (Path.home() / ".opencode" / "bin" / "opencode"))
    model = str(oc.get("model") or "deepseek/deepseek-flash")
    timeout = int(oc.get("timeout_seconds") or 600)
    runtime_root = Path(oc.get("runtime_root") or (Path.home() / ".hermes" / "opencode-email-runtime")).expanduser().resolve()
    runtime_config = runtime_root / "config"
    aggregate_id = hashlib.sha256(str(workdir).encode("utf-8")).hexdigest()[:16]
    runtime_instance = runtime_root / "aggregations" / aggregate_id
    runtime_home = runtime_instance / "home"
    runtime_data = runtime_instance / "data"
    runtime_cache = runtime_instance / "cache"
    runtime_state = runtime_instance / "state"
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

    run_dir = output_dir / "_run"
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("events.jsonl", "stderr.txt"):
        (run_dir / name).unlink(missing_ok=True)

    input_ids_json = json.dumps(pipeline_ids, ensure_ascii=False, separators=(",", ":"))
    command = [
        executable, "run", "--format", "json",
        "--agent", "mail-daily-aggregator",
        "--model", model,
        "--dir", str(workdir),
        "--title", f"mail-daily:{workdir.name}",
        "The exact validated aggregation input is INPUT_PIPELINE_IDS_JSON="
        f"{input_ids_json}. Read only emails/<pipeline_id>/summary.json for those IDs, "
        "then return the required compact daily JSON digest.",
    ]
    completed = subprocess.run(
        command, stdin=subprocess.DEVNULL, text=True, capture_output=True,
        timeout=timeout, env=env, check=False,
    )
    secure_write(run_dir / "events.jsonl", completed.stdout)
    if completed.stderr:
        secure_write(run_dir / "stderr.txt", completed.stderr)
    session_id, tools_used, result = parse_events(completed.stdout)
    if completed.returncode != 0 or result is None:
        raise RuntimeError(f"daily aggregation failed: {completed.stderr[-1000:]} {completed.stdout[-1000:]}")
    validate_daily_summary(result)
    output = build_aggregation_artifact(
        result,
        pipeline_ids,
        earliest_email_at=earliest_email_at,
        latest_email_at=latest_email_at,
        session_id=session_id,
        model=model,
        tools_used=tools_used,
    )
    output_path = output_dir / aggregation_filename(output["generated_at"])
    if output_path.exists():
        raise RuntimeError(f"aggregation output already exists: {output_path}")
    secure_write(output_path, json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "summary_path": str(output_path), "session_id": session_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
