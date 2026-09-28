#!/usr/bin/env python3
"""Run one isolated OpenCode session for one stable pipeline email ID."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR / "daily-mail-pipeline.yaml"


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


def resolve_email_key(cfg: dict[str, Any]) -> str:
    api = cfg.get("api") or {}
    env_name = str(api.get("api_key_env") or "EMAIL_SUMMARY_DEEPSEEK_API_KEY")
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    env_path = Path.home() / ".hermes" / ".env"
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"{env_name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"email summary credential not found: {env_name}")


def walk_values(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    matches = re.findall(r"\{[\s\S]*\}", text)
    for candidate in reversed(matches):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def parse_events(stdout: str) -> tuple[str | None, list[str], dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    session_id: str | None = None
    tools: list[str] = []
    text_candidates: list[str] = []
    for event in events:
        for item in walk_values(event):
            for key in ("sessionID", "sessionId", "session_id"):
                if isinstance(item.get(key), str):
                    session_id = item[key]
            tool_name = item.get("tool") or item.get("toolName") or item.get("name")
            item_type = str(item.get("type") or "").lower()
            if isinstance(tool_name, str) and "tool" in item_type and tool_name.startswith("mail_"):
                tools.append(tool_name)
            if isinstance(item.get("text"), str):
                text_candidates.append(item["text"])
    final: dict[str, Any] | None = None
    for text in reversed(text_candidates):
        final = parse_json_object(text)
        if final and "summary" in final:
            break
    return session_id, list(dict.fromkeys(tools)), final


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Analyze one indexed email with an OpenCode agent.")
    parser.add_argument("--pipeline-id", required=True)
    parser.add_argument("--mail-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--title")
    args = parser.parse_args()

    cfg = load_config(args.config.expanduser().resolve())
    opencode_cfg = cfg.get("opencode") or {}
    executable = str(opencode_cfg.get("executable") or (Path.home() / ".opencode" / "bin" / "opencode"))
    agent = str(opencode_cfg.get("agent") or "mail-analyzer")
    model = str(opencode_cfg.get("model") or "deepseek/deepseek-flash")
    timeout = int(opencode_cfg.get("timeout_seconds") or 600)
    runtime_root = Path(
        opencode_cfg.get("runtime_root")
        or (Path.home() / ".hermes" / "opencode-email-runtime")
    ).expanduser().resolve()
    runtime_config = runtime_root / "config"

    workspace = args.mail_dir.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    request_path = workspace / "request.json"
    if not workspace.is_dir() or not request_path.is_file():
        raise RuntimeError(f"invalid fixed email workspace: {workspace}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    pipeline_id = str(request.get("pipeline_id") or "")
    if not pipeline_id or pipeline_id != args.pipeline_id:
        raise RuntimeError("pipeline_id does not match request.json")
    if output_path != workspace and workspace not in output_path.parents:
        raise RuntimeError("output path must stay inside the agent work directory")
    runtime_instance = runtime_root / "sessions" / pipeline_id
    runtime_home = runtime_instance / "home"
    runtime_data = runtime_instance / "data"
    runtime_cache = runtime_instance / "cache"
    runtime_state = runtime_instance / "state"
    for path in (runtime_home, runtime_config / "opencode", runtime_data, runtime_cache, runtime_state):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = workspace / "opencode-run"
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("events.jsonl", "stderr.txt", "metadata.json", "timeout.txt"):
        (run_dir / name).unlink(missing_ok=True)

    prompt = (
        "Process the single email authorized by request.json. "
        "You must call mail_fetch and mail_unpack, inspect all relevant MIME parts and attachments, "
        "use link inspection only when it helps verify an event or deadline, and return the exact JSON schema "
        "required by the mail-analyzer agent. Do not output Markdown."
    )
    command = [
        executable,
        "run",
        "--format", "json",
        "--agent", agent,
        "--model", model,
        "--dir", str(workspace),
        "--title", args.title or f"mail:{pipeline_id}",
        prompt,
    ]
    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = resolve_email_key(cfg)
    env["HERMES_HOME"] = str(Path.home() / ".hermes")
    env["HIMALAYA_CONFIG"] = str(
        Path(
            opencode_cfg.get("himalaya_config")
            or (Path.home() / ".config" / "himalaya" / "config.toml")
        ).expanduser().resolve()
    )
    env["PATH"] = os.pathsep.join([
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin"),
        env.get("PATH", ""),
    ])
    env["HOME"] = str(runtime_home)
    env["XDG_CONFIG_HOME"] = str(runtime_config)
    env["XDG_DATA_HOME"] = str(runtime_data)
    env["XDG_CACHE_HOME"] = str(runtime_cache)
    env["XDG_STATE_HOME"] = str(runtime_state)
    env["OPENCODE_CONFIG_DIR"] = str(runtime_config / "opencode")
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        secure_write(run_dir / "timeout.txt", str(exc))
        raise RuntimeError(f"OpenCode timed out after {timeout}s") from exc

    secure_write(run_dir / "events.jsonl", completed.stdout)
    if completed.stderr:
        secure_write(run_dir / "stderr.txt", completed.stderr)
    session_id, tools_used, result = parse_events(completed.stdout)
    processor = {
        "processor": "opencode",
        "pipeline_id": pipeline_id,
        "run_id": run_id,
        "session_id": session_id,
        "model": model,
        "tools_used": tools_used,
        "returncode": completed.returncode,
    }
    secure_write(run_dir / "metadata.json", json.dumps({
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "session_id": session_id,
        "model": model,
        "tools_used": tools_used,
        "returncode": completed.returncode,
    }, ensure_ascii=False, indent=2))
    if completed.returncode != 0 or result is None:
        print(json.dumps({"ok": False, "pipeline_id": pipeline_id, "processor": processor}, ensure_ascii=False, indent=2))
        return 1
    output = {
        "schema_version": 3,
        "artifact_type": "mail_individual_summary",
        "pipeline_id": pipeline_id,
        "processor": processor,
        "analysis": result,
    }
    secure_write(output_path, json.dumps(output, ensure_ascii=False, indent=2))
    index_database = request.get("index_database")
    if index_database:
        MailIdentityIndex(Path(index_database)).set_summarized(pipeline_id, True)
    print(json.dumps({
        "ok": True,
        "pipeline_id": pipeline_id,
        "output": str(output_path),
        "processor": processor,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
