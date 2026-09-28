"""Central filesystem layout. Override every root with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

# Canonical roots. No other module should construct pipeline-owned home paths.
CODE_ROOT_ENV = "EMAIL_PIPELINE_CODE_ROOT"
CONFIG_ROOT_ENV = "EMAIL_PIPELINE_CONFIG_ROOT"
DATA_ROOT_ENV = "EMAIL_PIPELINE_DATA_ROOT"

DEFAULT_CODE_ROOT = Path.home() / "email-pipeline-code"
DEFAULT_CONFIG_ROOT = Path.home() / ".email-pipeline"
DEFAULT_DATA_ROOT = Path.home() / "email-pipeline"


def _root(env_name: str, default: Path) -> Path:
    return Path(os.environ.get(env_name) or default).expanduser().resolve()


def code_root() -> Path:
    return _root(CODE_ROOT_ENV, DEFAULT_CODE_ROOT)


def config_root() -> Path:
    return _root(CONFIG_ROOT_ENV, DEFAULT_CONFIG_ROOT)


def data_root() -> Path:
    return _root(DATA_ROOT_ENV, DEFAULT_DATA_ROOT)


def config_path() -> Path:
    return config_root() / "daily-mail-pipeline.toml"


def credential_env_path() -> Path:
    return config_root() / ".env"


def database_path() -> Path:
    return data_root() / "mail-index.sqlite3"


def daily_root() -> Path:
    return data_root() / "daily"


def salt_path() -> Path:
    return config_root() / "pipeline-id-salt"


def token_refresh_path() -> Path:
    return config_root() / "outlook-token-refresh.sh"


def opencode_runtime_root() -> Path:
    return data_root() / "opencode-runtime"


def entrypoint_path() -> Path:
    return code_root() / "email-pipeline.py"
