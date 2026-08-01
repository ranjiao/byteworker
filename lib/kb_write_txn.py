"""Shared locking and rollback primitives for KB content/Git writers."""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping


LOCK_FILENAME = "byteworker-write.lock"


@contextmanager
def kb_write_lock(kb: Path) -> Iterator[BinaryIO]:
    """Serialize every writer that mutates durable KB content or its Git index."""

    lock_path = kb.resolve() / ".git" / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    return {
        path: path.read_bytes() if path.exists() else None
        for path in dict.fromkeys(paths)
    }


def restore_files(snapshots: Mapping[Path, bytes | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            atomic_write(path, content)


def snapshot_git_index(kb: Path) -> bytes | None:
    path = kb.resolve() / ".git" / "index"
    return path.read_bytes() if path.exists() else None


def restore_git_index(kb: Path, content: bytes | None) -> None:
    path = kb.resolve() / ".git" / "index"
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        atomic_write(path, content)
