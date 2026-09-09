"""Run a child process without retaining unbounded output in memory."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Sequence

from machine_protocol import (
    ARTIFACT_PROTOCOL,
    INLINE_STDOUT_LIMIT_BYTES,
    STDERR_CAPTURE_LIMIT_BYTES,
)



@dataclass(frozen=True)
class CapturedProcess:
    returncode: int
    stdout: str
    stderr: str
    artifact: dict[str, object] | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _content_type(path: Path) -> str:
    with path.open("rb") as stream:
        prefix = stream.read(4096).lstrip()
    if prefix.startswith((b"{", b"[")):
        return "application/json"
    return "text/plain"


class _StdoutCollector:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.inline = bytearray()
        self.path: Path | None = None
        self.stream: BinaryIO | None = None

    def write(self, chunk: bytes) -> None:
        if self.stream is not None:
            self.stream.write(chunk)
            return
        if len(self.inline) + len(chunk) <= self.limit:
            self.inline.extend(chunk)
            return
        descriptor, name = tempfile.mkstemp(
            prefix="byteworker-cli-output-", suffix=".out"
        )
        self.path = Path(name)
        os.chmod(self.path, 0o600)
        self.stream = os.fdopen(descriptor, "wb")
        self.stream.write(self.inline)
        self.inline.clear()
        self.stream.write(chunk)

    def finish(self) -> tuple[str, dict[str, object] | None]:
        if self.stream is None or self.path is None:
            return self.inline.decode("utf-8", errors="replace"), None
        self.stream.flush()
        self.stream.close()
        self.stream = None
        size = self.path.stat().st_size
        return "", {
            "protocol": ARTIFACT_PROTOCOL,
            "artifact_path": str(self.path),
            "bytes": size,
            "sha256": _sha256(self.path),
            "content_type": _content_type(self.path),
            "mode": "0600",
            "temporary": True,
        }

    def cleanup(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)


class _LimitedCollector:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.value = bytearray()

    def write(self, chunk: bytes) -> None:
        remaining = self.limit - len(self.value)
        if remaining > 0:
            self.value.extend(chunk[:remaining])

    def text(self) -> str:
        return self.value.decode("utf-8", errors="replace")


def _drain(
    stream: BinaryIO,
    write: Callable[[bytes], None],
    errors: list[BaseException],
) -> None:
    failed = False
    try:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            if failed:
                continue
            try:
                write(chunk)
            except BaseException as exc:  # drain the pipe before propagating
                errors.append(exc)
                failed = True
    finally:
        stream.close()


def run_bounded_output(
    argv: Sequence[str],
    *,
    inline_stdout_bytes: int = INLINE_STDOUT_LIMIT_BYTES,
    stderr_bytes: int = STDERR_CAPTURE_LIMIT_BYTES,
) -> CapturedProcess:
    """Capture small stdout inline and retain large stdout as a private artifact."""
    if inline_stdout_bytes < 1 or stderr_bytes < 1:
        raise ValueError("output limits must be positive")

    stdout_collector = _StdoutCollector(inline_stdout_bytes)
    stderr_collector = _LimitedCollector(stderr_bytes)
    errors: list[BaseException] = []
    try:
        process = subprocess.Popen(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert process.stdout is not None
        assert process.stderr is not None
        threads = (
            threading.Thread(
                target=_drain,
                args=(process.stdout, stdout_collector.write, errors),
            ),
            threading.Thread(
                target=_drain,
                args=(process.stderr, stderr_collector.write, errors),
            ),
        )
        for thread in threads:
            thread.start()
        returncode = process.wait()
        for thread in threads:
            thread.join()
        if errors:
            raise errors[0]
        stdout, artifact = stdout_collector.finish()
        return CapturedProcess(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr_collector.text(),
            artifact=artifact,
        )
    except BaseException:
        stdout_collector.cleanup()
        raise
