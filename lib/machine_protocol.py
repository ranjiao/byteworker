"""Stable machine-readable envelope for byteworker's deterministic CLIs."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, TextIO


PROTOCOL_VERSION = "byteworker-cli/v1"
ARTIFACT_PROTOCOL = "byteworker-cli-artifact/v1"
INLINE_STDOUT_LIMIT_BYTES = 1024 * 1024
STDERR_CAPTURE_LIMIT_BYTES = 64 * 1024


def output_policy() -> dict[str, Any]:
    return {
        "inline_stdout_limit_bytes": INLINE_STDOUT_LIMIT_BYTES,
        "stderr_capture_limit_bytes": STDERR_CAPTURE_LIMIT_BYTES,
        "artifact_protocol": ARTIFACT_PROTOCOL,
        "artifact_docs": "references/command-output.md",
    }


def context(
    *,
    tool: str,
    operation: str,
    execution_time_ms: int,
    command_path: str = "",
    stability: str = "",
    side_effect: str = "",
) -> dict[str, Any]:
    result = {
        "protocol": PROTOCOL_VERSION,
        "tool": tool,
        "operation": operation,
        "execution_time_ms": max(0, execution_time_ms),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if command_path:
        result["command_path"] = command_path
    if stability:
        result["stability"] = stability
    if side_effect:
        result["side_effect"] = side_effect
    return result


def error_payload(
    *,
    code: str,
    message: str,
    hint: str = "",
    details: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if hint:
        result["hint"] = hint
    if details is not None:
        result["details"] = details
    return result


def envelope(
    *,
    status: str,
    data: Any,
    error: dict[str, Any] | None,
    context_value: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "data": data,
        "error": error,
        "context": context_value,
    }


def write_envelope(
    stream: TextIO,
    payload: dict[str, Any],
    *,
    pretty: bool = False,
) -> None:
    stream.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    )
