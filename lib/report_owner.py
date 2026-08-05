"""Cross-scheduler lock for report owner migration."""

from __future__ import annotations

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _secure_chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except PermissionError:
        try:
            current = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            raise
        if (current & 0o077) != 0:
            raise


@contextmanager
def report_owner_lock(kb: Path) -> Iterator[None]:
    root = kb.expanduser().resolve() / "state"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "report-owner.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    _secure_chmod(path, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
