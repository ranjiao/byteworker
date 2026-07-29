"""Persistent state for byteworker's non-blocking skill update checks."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


STATE_VERSION = 1


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_attempt_at": 0,
        "last_success_at": 0,
        "failure_count": 0,
        "last_failure_code": "",
        "next_retry_at": 0,
        "last_checked_commit": "",
        "postflight_pending": False,
        "postflight_commit": "",
        "postflight_failure_count": 0,
        "postflight_last_failure_code": "",
        "postflight_next_retry_at": 0,
    }


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _normalize(raw: object) -> dict[str, Any]:
    state = empty_state()
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return state
    for key in (
        "last_attempt_at",
        "last_success_at",
        "failure_count",
        "next_retry_at",
        "postflight_failure_count",
        "postflight_next_retry_at",
    ):
        state[key] = _non_negative_int(raw.get(key))
    for key in (
        "last_failure_code",
        "last_checked_commit",
        "postflight_commit",
        "postflight_last_failure_code",
    ):
        value = raw.get(key)
        state[key] = value if isinstance(value, str) else ""
    state["postflight_pending"] = raw.get("postflight_pending") is True
    return state


def load_state(path: Path, legacy_stamp: Path | None = None) -> dict[str, Any]:
    try:
        return _normalize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        state = empty_state()
    if legacy_stamp is not None:
        try:
            state["last_attempt_at"] = _non_negative_int(
                legacy_stamp.read_text(encoding="utf-8").strip()
            )
        except OSError:
            pass
    return state


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_normalize(state), ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def update_due(
    state: dict[str, Any],
    *,
    now: int,
    interval: int,
    force: bool = False,
) -> bool:
    if force:
        return True
    if _non_negative_int(state.get("failure_count")) > 0:
        return now >= _non_negative_int(state.get("next_retry_at"))
    last_success = _non_negative_int(state.get("last_success_at"))
    return last_success == 0 or now - last_success >= max(0, interval)


def record_attempt(state: dict[str, Any], *, now: int) -> dict[str, Any]:
    result = _normalize(state)
    result["last_attempt_at"] = max(0, now)
    return result


def record_success(
    state: dict[str, Any], *, now: int, commit: str = ""
) -> dict[str, Any]:
    result = record_attempt(state, now=now)
    result["last_success_at"] = max(0, now)
    result["failure_count"] = 0
    result["last_failure_code"] = ""
    result["next_retry_at"] = 0
    if commit:
        result["last_checked_commit"] = commit
    return result


def _retry_delay(failure_count: int, base: int, maximum: int) -> int:
    exponent = min(max(0, failure_count - 1), 10)
    return min(max(0, maximum), max(0, base) * (2**exponent))


def record_failure(
    state: dict[str, Any],
    *,
    now: int,
    code: str,
    retry_base: int,
    retry_max: int,
) -> dict[str, Any]:
    result = record_attempt(state, now=now)
    count = _non_negative_int(result.get("failure_count")) + 1
    result["failure_count"] = count
    result["last_failure_code"] = code
    result["next_retry_at"] = max(0, now) + _retry_delay(
        count, retry_base, retry_max
    )
    return result


def mark_postflight_pending(
    state: dict[str, Any], *, commit: str = ""
) -> dict[str, Any]:
    result = _normalize(state)
    result["postflight_pending"] = True
    result["postflight_commit"] = commit
    result["postflight_failure_count"] = 0
    result["postflight_last_failure_code"] = ""
    result["postflight_next_retry_at"] = 0
    return result


def postflight_due(state: dict[str, Any], *, now: int) -> bool:
    return state.get("postflight_pending") is True and now >= _non_negative_int(
        state.get("postflight_next_retry_at")
    )


def record_postflight_success(state: dict[str, Any]) -> dict[str, Any]:
    result = _normalize(state)
    result["postflight_pending"] = False
    result["postflight_commit"] = ""
    result["postflight_failure_count"] = 0
    result["postflight_last_failure_code"] = ""
    result["postflight_next_retry_at"] = 0
    return result


def record_postflight_failure(
    state: dict[str, Any],
    *,
    now: int,
    code: str,
    retry_base: int,
    retry_max: int,
) -> dict[str, Any]:
    result = _normalize(state)
    result["postflight_pending"] = True
    count = _non_negative_int(result.get("postflight_failure_count")) + 1
    result["postflight_failure_count"] = count
    result["postflight_last_failure_code"] = code
    result["postflight_next_retry_at"] = max(0, now) + _retry_delay(
        count, retry_base, retry_max
    )
    return result
