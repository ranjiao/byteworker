"""Read execution evidence from the local Doubao Work tool trajectory."""

from __future__ import annotations

import json
import re
from pathlib import Path


_SESSION_ID = re.compile(r"\d{10,30}")
_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TOOLS = {"create_agent": "hint", "send_message": "message"}
_CODES = {
    "context_length_exceeded": "DIGEST_HOST_CONTEXT_LIMIT",
    "context_window_exceeded": "DIGEST_HOST_CONTEXT_LIMIT",
    "prompt_too_long": "DIGEST_HOST_CONTEXT_LIMIT",
    "timeout": "DIGEST_WORKER_TIMEOUT",
    "worker_unavailable": "DIGEST_WORKER_UNAVAILABLE",
    "worker_failed": "DIGEST_WORKER_FAILED",
}
_MAX_TRACE_BYTES = 32 * 1024 * 1024


def _trace_root() -> Path:
    return (
        Path.home() / "Library/Application Support/DoubaoWork/Default/.doubaowork"
        / "agent_mode/workspace/.sessions"
    )


def _failure_code(tool: str, content: str) -> tuple[str, int | None]:
    try:
        payload = json.loads(content)
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code", "")).lower()
        if code not in _CODES:
            return "", None
        available = error.get("available_input_tokens") if _CODES[code] == "DIGEST_HOST_CONTEXT_LIMIT" else None
        return code, available if type(available) is int and available > 0 else None
    if not content.startswith(f"{tool} failed:"):
        return "", None
    reason = content.split("\n", 1)[0].lower()
    if "organizer(s) may be created" in reason:
        return "worker_unavailable", None
    if any(text in reason for text in (
        "context_length_exceeded", "context length exceeded", "maximum context length",
        "context_window_exceeded", "context window exceeded", "prompt_too_long", "prompt too long",
    )):
        return "context_length_exceeded", None
    if "timed out" in reason:
        return "timeout", None
    return "worker_failed", None


def _read_trajectory(path: Path, attempt_id: str, shard_path: Path) -> dict[str, str | int] | None:
    calls = {}
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - _MAX_TRACE_BYTES))
        if size > _MAX_TRACE_BYTES:
            stream.readline()
        for line in stream:
            if attempt_id.encode() not in line and not calls:
                continue
            try:
                row = json.loads(line)
            except (UnicodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get("role") == "assistant":
                for call in row.get("tool_calls", []):
                    function = call.get("function", {})
                    tool = function.get("name")
                    call_id = str(call.get("id", ""))
                    if tool not in _TOOLS or not _CALL_ID.fullmatch(call_id):
                        continue
                    try:
                        arguments = json.loads(function.get("arguments", "{}"))
                    except (TypeError, ValueError):
                        continue
                    prompt = arguments.get(_TOOLS[tool], "") if isinstance(arguments, dict) else ""
                    if isinstance(prompt, str) and attempt_id in prompt and str(shard_path) in prompt:
                        calls[call_id] = tool
            elif row.get("role") == "tool":
                call_id = row.get("tool_call_id")
                tool = calls.pop(call_id, None)
                content = row.get("content")
                if tool and isinstance(content, str):
                    provider_code, available = _failure_code(tool, content)
                    if provider_code:
                        return {
                            "host": "doubaowork", "tool": tool, "call_id": call_id,
                            "provider_code": provider_code, "error_code": _CODES[provider_code],
                            **({"available_input_tokens": available} if available is not None else {}),
                        }
    return None


def read_host_failure(attempt_id: str, shard_path: Path, *, session_id: str = "") -> dict[str, str | int] | None:
    root = _trace_root()
    if session_id and not _SESSION_ID.fullmatch(session_id):
        return None
    pattern = f"{session_id or '*'}/agents/[mo]_*/system/trajectory.jsonl"
    try:
        paths = [
            (path.stat().st_mtime, path) for path in root.glob(pattern)
            if path.resolve() == path and _SESSION_ID.fullmatch(path.relative_to(root).parts[0])
        ]
        for _, path in sorted(paths, reverse=True)[:8]:
            evidence = _read_trajectory(path, attempt_id, shard_path)
            if evidence is not None:
                return evidence
    except OSError:
        return None
    return None
