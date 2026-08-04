"""Cross-scheduler lock for report owner migration."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def report_owner_lock(kb: Path) -> Iterator[None]:
    root = kb.expanduser().resolve() / "state"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "report-owner.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
