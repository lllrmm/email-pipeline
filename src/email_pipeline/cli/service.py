"""Manage per-instance user systemd services."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

from email_pipeline.paths import code_root, instance_root
from email_pipeline.config import default_config_path, load_config


def unit_name(root: Path, component: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-") or "instance"
    digest = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return f"email-pipeline-{slug}-{digest}-{component}.service"


def unit_text(root: Path, component: str, watch_name: str) -> str:
    dependency = watch_name if component == "queue" else "network-online.target"
    after = f"network-online.target {dependency}" if component == "queue" else "network-online.target"
    command = "consume" if component == "queue" else "watch"
    return (
        "[Unit]\n"
        f"Description=Email pipeline {component} service for {root}\n"
        f"After={after}\n"
        "Wants=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={root}\n"
        f"ExecStart=/usr/bin/python3 {code_root() / 'email-pipeline.py'} {command}\n"
        "Restart=always\nRestartSec=15\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        f"Environment=EMAIL_PIPELINE_CODE_ROOT={code_root()}\n"
        f"Environment=EMAIL_PIPELINE_INSTANCE_ROOT={root}\n"
        f"Environment=PYTHONPATH={code_root() / 'src'}:{code_root() / 'vendor'}\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage this instance's systemd service.")
    parser.add_argument("action", choices=("start", "stop", "restart", "enable", "disable", "status"))
    args = parser.parse_args()
    root = instance_root()
    if root is None:
        parser.error("run from an initialized instance directory")
    config = load_config(default_config_path())
    watcher_enabled = bool((config.get("watcher") or {}).get("enabled", False))
    names = {"queue": unit_name(root, "queue"), "watch": unit_name(root, "watch")}
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    if args.action in {"start", "restart", "enable", "status"}:
        unit_dir.mkdir(parents=True, exist_ok=True)
        for component in ("queue", "watch"):
            unit_path = unit_dir / names[component]
            unit_path.write_text(unit_text(root, component, names["watch"]), encoding="utf-8")
            unit_path.chmod(0o600)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    actions = {"queue": args.action, "watch": args.action if watcher_enabled else ("stop" if args.action in {"start", "restart"} else "disable" if args.action == "enable" else args.action)}
    result = 0
    for component, action in actions.items():
        result = max(result, subprocess.run(["systemctl", "--user", action, names[component]], check=False).returncode)
    return result
