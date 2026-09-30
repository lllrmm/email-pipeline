"""Non-blocking process locks for singleton pipeline components."""

from __future__ import annotations

import fcntl
from pathlib import Path

from .paths import data_root


class ProcessLock:
    def __init__(self, component: str) -> None:
        lock_dir = data_root() / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_dir.chmod(0o700)
        self.path = lock_dir / f"{component}.lock"
        self.handle = self.path.open("a+", encoding="ascii")
        self.path.chmod(0o600)
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise RuntimeError(f"{component} is already running (lock: {self.path})") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={__import__('os').getpid()}\n")
        self.handle.flush()

    def close(self) -> None:
        if not self.handle.closed:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()

    def __enter__(self) -> "ProcessLock":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
