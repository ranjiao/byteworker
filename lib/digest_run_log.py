"""Private, structured timing logs for end-to-end digest runs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


RUN_EVENT_SCHEMA = "byteworker-digest-run-event/v1"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_STALE_AFTER = timedelta(hours=6)
MAX_EVENTS_PER_QUERY = 1000
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
RUN_ID_RE = re.compile(r"^DG-\d{8}T\d{6}Z-[0-9a-f]{8}$")
MACHINE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CALL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

SOURCE_TYPES = {
    "unknown",
    "feishu_doc",
    "feishu_minutes",
    "feishu_meeting",
    "feishu_chat",
    "meego",
    "feishu_base",
    "aeolus",
    "web",
    "local_md",
}
STAGE_ACTIONS = {
    "classify": "classify input and select the source workflow",
    "capture": "fetch complete source artifacts",
    "bundle": "normalize SourceBundle components and anchors",
    "preflight": "compute payload identity and idempotency state",
    "analysis_prepare": "build one compact semantic analysis packet",
    "dependency_review": "review important dependency boundaries",
    "conflict_review": "compare candidates with existing knowledge",
    "semantic_analysis": "extract facts, entities, decisions, and evidence",
    "candidate_generation": "render node candidates and the digest plan",
    "transaction_validate": "validate plan, baselines, links, and provenance",
    "transaction": "write raw, provenance, nodes, index, journal, and commit",
    "finalize": "verify the receipt and prepare the compact result",
}
STAGE_STATUSES = {"started", "completed", "failed"}
RUN_STATUSES = {"committed", "noop", "failed", "cancelled"}
WAIT_REASONS = {
    "CONFLICT_DECISION_REQUIRED",
    "DEPENDENCY_APPROVAL_REQUIRED",
    "DIGEST_SCOPE_CONFIRMATION_REQUIRED",
    "SOURCE_AUTH_REQUIRED",
    "USER_INPUT_REQUIRED",
}
USAGE_SOURCES = {"measured", "estimated"}
WORKER_ROLES = {
    "coordinator",
    "dependency_worker",
    "semantic_worker",
    "conflict_worker",
    "final_reducer",
}
USAGE_FIELDS = {
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
}
METRIC_FIELDS = {
    "item_count",
    "component_count",
    "input_bytes",
    "output_count",
    "warning_count",
    "retry_count",
    "page_count",
    "node_count",
    "evidence_count",
    "worker_count",
    "shard_count",
}


class DigestRunError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.hint:
            value["hint"] = self.hint
        if self.details:
            value["details"] = self.details
        return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _state_root(kb: Path) -> Path:
    return kb.expanduser().resolve() / "state" / "digest"


def _log_root(kb: Path) -> Path:
    return _state_root(kb) / "run-logs"


def _ensure_state_ignored(kb: Path) -> None:
    info_exclude = kb.expanduser().resolve() / ".git" / "info" / "exclude"
    if not info_exclude.parent.is_dir():
        raise DigestRunError(
            "DIGEST_RUN_KB_INVALID",
            "KB must be a local Git repository before digest logging can start.",
        )
    current = info_exclude.read_text(encoding="utf-8") if info_exclude.exists() else ""
    if any(line.strip() == "/state/" for line in current.splitlines()):
        return
    with info_exclude.open("a", encoding="utf-8") as handle:
        if current and not current.endswith("\n"):
            handle.write("\n")
        handle.write("/state/\n")


def _ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise DigestRunError(
            "DIGEST_RUN_PATH_INVALID",
            "Digest run log directory cannot be a symbolic link.",
        )
    path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    os.chmod(path, DIRECTORY_MODE)


@contextmanager
def _log_lock(kb: Path) -> Iterator[Path]:
    _ensure_state_ignored(kb)
    root = _log_root(kb)
    state = root.parents[1]
    _ensure_directory(state)
    _ensure_directory(root.parent)
    _ensure_directory(root)
    lock_path = root / ".lock"
    if lock_path.is_symlink():
        raise DigestRunError(
            "DIGEST_RUN_PATH_INVALID",
            "Digest run log lock cannot be a symbolic link.",
        )
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, FILE_MODE)
    os.chmod(lock_path, FILE_MODE)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield root
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise DigestRunError("DIGEST_RUN_INVALID_ID", "Invalid digest run_id.")
    return run_id


def _validate_detail_code(detail_code: str) -> str:
    if detail_code and not MACHINE_CODE_RE.fullmatch(detail_code):
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "detail_code must be an uppercase stable machine code.",
        )
    return detail_code


def _validate_metrics(metrics: Mapping[str, Any] | None) -> dict[str, int]:
    if metrics is None:
        return {}
    unknown = sorted(set(metrics) - METRIC_FIELDS)
    if unknown:
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "Unknown digest run metrics: " + ", ".join(unknown),
        )
    result: dict[str, int] = {}
    for key, raw in metrics.items():
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise DigestRunError(
                "DIGEST_RUN_INVALID",
                f"Digest run metric {key} must be a non-negative integer.",
            )
        result[key] = raw
    return result


def _validate_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    unknown = sorted(set(usage) - USAGE_FIELDS)
    missing = sorted(USAGE_FIELDS - set(usage))
    if unknown or missing:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise DigestRunError(
            "DIGEST_RUN_USAGE_INVALID",
            "Usage fields are invalid: " + "; ".join(details),
        )
    result: dict[str, int] = {}
    for key in sorted(USAGE_FIELDS):
        value = usage[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DigestRunError(
                "DIGEST_RUN_USAGE_INVALID",
                f"Usage field {key} must be a non-negative integer.",
            )
        result[key] = value
    if result["cached_input_tokens"] > result["input_tokens"]:
        raise DigestRunError(
            "DIGEST_RUN_USAGE_INVALID",
            "cached_input_tokens cannot exceed input_tokens.",
        )
    return result


def _call_id_hash(call_id: str) -> str:
    if not CALL_ID_RE.fullmatch(call_id):
        raise DigestRunError(
            "DIGEST_RUN_USAGE_INVALID",
            "call_id must be a stable opaque identifier.",
        )
    return "sha256:" + hashlib.sha256(call_id.encode("utf-8")).hexdigest()


def _log_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.glob("*.jsonl"):
        if path.is_symlink():
            raise DigestRunError(
                "DIGEST_RUN_PATH_INVALID",
                "Digest run log file cannot be a symbolic link.",
            )
        if path.is_file():
            result.append(path)
    return sorted(result)


def _read_events_from_root(root: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in _log_files(root):
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                if index == len(lines) - 1 and not content.endswith("\n"):
                    continue
                raise DigestRunError(
                    "DIGEST_RUN_LOG_INVALID",
                    f"Digest run log JSON is invalid: {path.name}",
                ) from exc
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != RUN_EVENT_SCHEMA
            ):
                raise DigestRunError(
                    "DIGEST_RUN_LOG_INVALID",
                    f"Digest run log schema is invalid: {path.name}",
                )
            events.append(value)
    events.sort(key=lambda item: str(item.get("timestamp", "")))
    return events


def _read_events(kb: Path) -> list[dict[str, Any]]:
    root = _log_root(kb)
    if not root.exists():
        return []
    return _read_events_from_root(root)


def _prune(root: Path, *, now: datetime) -> None:
    cutoff = now.astimezone(timezone.utc).date() - timedelta(
        days=DEFAULT_RETENTION_DAYS
    )
    for path in _log_files(root):
        try:
            day = datetime.strptime(path.name[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()


def _target(root: Path, *, now: datetime, size: int) -> Path:
    prefix = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    index = 0
    while True:
        suffix = "" if index == 0 else f"-{index:04d}"
        candidate = root / f"{prefix}{suffix}.jsonl"
        current_size = candidate.stat().st_size if candidate.exists() else 0
        if current_size + size <= DEFAULT_MAX_FILE_BYTES:
            return candidate
        index += 1


def _append(root: Path, event: Mapping[str, Any], *, now: datetime) -> str:
    payload = (
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    _prune(root, now=now)
    target = _target(root, now=now, size=len(payload))
    descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
    try:
        os.fchmod(descriptor, FILE_MODE)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return str(target.relative_to(root.parents[2]))


def _run_events(events: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    return [event for event in events if event.get("run_id") == run_id]


def _latest_source_type(events: list[dict[str, Any]]) -> str:
    return next(
        (
            str(event.get("source_type"))
            for event in reversed(events)
            if event.get("source_type") not in {None, "", "unknown"}
        ),
        str(events[0].get("source_type", "unknown")),
    )


def _lifecycle_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event") != "usage_recorded"]


def _is_terminal(event: Mapping[str, Any]) -> bool:
    return event.get("event") in {"completed", "failed", "cancelled"}


def _is_waiting(events: list[dict[str, Any]]) -> bool:
    return bool(events) and events[-1].get("event") == "waiting_user"


def _is_stale(events: list[dict[str, Any]], *, now: datetime) -> bool:
    if not events or _is_terminal(events[-1]) or _is_waiting(events):
        return False
    updated_at = _parse_time(events[-1].get("timestamp"))
    return updated_at is not None and now - updated_at > DEFAULT_STALE_AFTER


def _require_active_run(
    events: list[dict[str, Any]],
    run_id: str,
    *,
    now: datetime | None = None,
    allow_waiting: bool = False,
) -> list[dict[str, Any]]:
    selected = _run_events(events, _validate_run_id(run_id))
    if not selected:
        raise DigestRunError("DIGEST_RUN_NOT_FOUND", f"Digest run not found: {run_id}")
    lifecycle = _lifecycle_events(selected)
    if _is_terminal(lifecycle[-1]):
        raise DigestRunError(
            "DIGEST_RUN_TERMINAL",
            f"Digest run is already terminal: {run_id}",
        )
    if _is_waiting(lifecycle) and not allow_waiting:
        raise DigestRunError(
            "DIGEST_RUN_WAITING_USER",
            "Digest run is waiting for user input; resume it before continuing.",
        )
    current = now or _utc_now()
    if _is_stale(lifecycle, now=current):
        raise DigestRunError(
            "DIGEST_RUN_STALE",
            "Digest run is stale; resume it before continuing.",
        )
    return selected


def _active_duration_ms(events: list[dict[str, Any]], *, now: datetime) -> int:
    lifecycle = _lifecycle_events(events)
    if not lifecycle:
        return 0
    active_from = _parse_time(lifecycle[0].get("timestamp"))
    previous_time = active_from
    total = timedelta(0)
    for event in lifecycle[1:]:
        event_time = _parse_time(event.get("timestamp"))
        if event_time is None:
            continue
        event_name = event.get("event")
        if event_name == "waiting_user" and active_from is not None:
            if event_time >= active_from:
                total += event_time - active_from
            active_from = None
        elif event_name == "resumed":
            if event.get("resume_from") == "stale" and active_from is not None:
                if previous_time is not None and previous_time >= active_from:
                    total += previous_time - active_from
                active_from = event_time
            elif active_from is None:
                active_from = event_time
        elif _is_terminal(event) and active_from is not None:
            if event_time >= active_from:
                total += event_time - active_from
            active_from = None
        previous_time = event_time
    if active_from is not None and not _is_terminal(lifecycle[-1]):
        end = now
        if _is_waiting(lifecycle):
            end = _parse_time(lifecycle[-1].get("timestamp")) or active_from
        elif _is_stale(lifecycle, now=now):
            end = _parse_time(lifecycle[-1].get("timestamp")) or active_from
        if end >= active_from:
            total += end - active_from
    return round(total.total_seconds() * 1000)


def _open_stages(events: list[dict[str, Any]]) -> list[str]:
    open_stages: set[str] = set()
    for event in events:
        stage = str(event.get("stage", ""))
        if event.get("event") == "stage_started":
            open_stages.add(stage)
        elif event.get("event") in {"stage_completed", "stage_failed"}:
            open_stages.discard(stage)
    return sorted(open_stages)


def _open_stage_duration_ms(
    events: list[dict[str, Any]],
    *,
    open_event: Mapping[str, Any],
    stage: str,
    now: datetime,
) -> int:
    try:
        start_index = events.index(open_event)  # type: ignore[arg-type]
    except ValueError:
        start_index = 0
    timing_events = [dict(open_event)]
    for event in events[start_index + 1 :]:
        if event.get("event") == "resumed" or (
            event.get("event") == "heartbeat" and event.get("stage") == stage
        ):
            timing_events.append(event)
    return _active_duration_ms(timing_events, now=now)


def start_run(
    kb: Path,
    *,
    source_type: str,
    source_ref: str = "",
    now: datetime | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    if source_type not in SOURCE_TYPES:
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "Unsupported digest source_type.",
            details={"allowed": sorted(SOURCE_TYPES)},
        )
    current = now or _utc_now()
    value_id = run_id or (
        "DG-" + current.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        + secrets.token_hex(4)
    )
    _validate_run_id(value_id)
    source_ref_hash = (
        "sha256:" + hashlib.sha256(source_ref.encode("utf-8")).hexdigest()
        if source_ref
        else ""
    )
    event = {
        "schema_version": RUN_EVENT_SCHEMA,
        "timestamp": _iso(current),
        "run_id": value_id,
        "event": "started",
        "stage": "classify",
        "action": STAGE_ACTIONS["classify"],
        "status": "running",
        "source_type": source_type,
        "source_ref_hash": source_ref_hash,
        "detail_code": "DIGEST_RUN_STARTED",
        "duration_ms": 0,
        "metrics": {},
    }
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        if _run_events(events, value_id):
            raise DigestRunError(
                "DIGEST_RUN_ALREADY_EXISTS", f"Digest run already exists: {value_id}"
            )
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def record_stage(
    kb: Path,
    *,
    run_id: str,
    stage: str,
    status: str,
    detail_code: str = "",
    source_type: str = "",
    metrics: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if stage not in STAGE_ACTIONS:
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "Unknown digest stage.",
            details={"allowed": sorted(STAGE_ACTIONS)},
        )
    if status not in STAGE_STATUSES:
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "Digest stage status must be started, completed, or failed.",
        )
    if status == "failed" and not detail_code:
        raise DigestRunError(
            "DIGEST_RUN_INVALID", "Failed digest stages require detail_code."
        )
    if source_type and source_type not in SOURCE_TYPES - {"unknown"}:
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "Unsupported digest source_type.",
            details={"allowed": sorted(SOURCE_TYPES - {"unknown"})},
        )
    _validate_detail_code(detail_code)
    metric_values = _validate_metrics(metrics)
    current = now or _utc_now()
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        selected = _require_active_run(events, run_id, now=current)
        duration_ms = 0
        open_event = next(
            (
                event
                for event in reversed(selected)
                if event.get("stage") == stage
                and event.get("event") == "stage_started"
            ),
            None,
        )
        last_terminal = next(
            (
                event
                for event in reversed(selected)
                if event.get("stage") == stage
                and event.get("event") in {"stage_completed", "stage_failed"}
            ),
            None,
        )
        has_open_stage = open_event is not None and (
            last_terminal is None
            or str(open_event.get("timestamp", ""))
            > str(last_terminal.get("timestamp", ""))
        )
        if status == "started" and has_open_stage:
            raise DigestRunError(
                "DIGEST_RUN_STAGE_ALREADY_STARTED",
                f"Digest stage already has an open start event: {stage}",
            )
        if status != "started":
            if open_event is None or (
                last_terminal is not None
                and str(last_terminal.get("timestamp", ""))
                > str(open_event.get("timestamp", ""))
            ):
                raise DigestRunError(
                    "DIGEST_RUN_STAGE_NOT_STARTED",
                    f"Digest stage has no open start event: {stage}",
                )
            started_at = _parse_time(open_event.get("timestamp"))
            if started_at is None or current < started_at:
                raise DigestRunError(
                    "DIGEST_RUN_TIME_INVALID",
                    "Digest stage timestamps are invalid.",
                )
            duration_ms = _open_stage_duration_ms(
                selected,
                open_event=open_event,
                stage=stage,
                now=current,
            )
        event_name = {
            "started": "stage_started",
            "completed": "stage_completed",
            "failed": "stage_failed",
        }[status]
        event = {
            "schema_version": RUN_EVENT_SCHEMA,
            "timestamp": _iso(current),
            "run_id": run_id,
            "event": event_name,
            "stage": stage,
            "action": STAGE_ACTIONS[stage],
            "status": status,
            "source_type": source_type or _latest_source_type(selected),
            "source_ref_hash": selected[0].get("source_ref_hash", ""),
            "detail_code": detail_code,
            "duration_ms": duration_ms,
            "metrics": metric_values,
        }
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def _current_stage(events: list[dict[str, Any]]) -> str:
    return next(
        (
            str(event.get("stage"))
            for event in reversed(_lifecycle_events(events))
            if event.get("stage") in STAGE_ACTIONS
        ),
        "classify",
    )


def wait_for_user(
    kb: Path,
    *,
    run_id: str,
    reason_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if reason_code not in WAIT_REASONS:
        raise DigestRunError(
            "DIGEST_RUN_WAIT_REASON_INVALID",
            "Unsupported waiting reason code.",
            details={"allowed": sorted(WAIT_REASONS)},
        )
    current = now or _utc_now()
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        selected = _require_active_run(events, run_id, now=current)
        open_stages = _open_stages(selected)
        if open_stages:
            raise DigestRunError(
                "DIGEST_RUN_STAGE_OPEN",
                "Close open stages before waiting for user: " + ", ".join(open_stages),
            )
        stage = _current_stage(selected)
        event = {
            "schema_version": RUN_EVENT_SCHEMA,
            "timestamp": _iso(current),
            "run_id": run_id,
            "event": "waiting_user",
            "stage": stage,
            "action": STAGE_ACTIONS[stage],
            "status": "waiting_user",
            "source_type": _latest_source_type(selected),
            "source_ref_hash": selected[0].get("source_ref_hash", ""),
            "detail_code": reason_code,
            "duration_ms": _active_duration_ms(selected, now=current),
            "metrics": {},
        }
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def resume_run(
    kb: Path,
    *,
    run_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _utc_now()
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        selected = _run_events(events, _validate_run_id(run_id))
        if not selected:
            raise DigestRunError(
                "DIGEST_RUN_NOT_FOUND", f"Digest run not found: {run_id}"
            )
        lifecycle = _lifecycle_events(selected)
        if _is_terminal(lifecycle[-1]):
            raise DigestRunError(
                "DIGEST_RUN_TERMINAL", f"Digest run is already terminal: {run_id}"
            )
        waiting = _is_waiting(lifecycle)
        stale = _is_stale(lifecycle, now=current)
        if not waiting and not stale:
            raise DigestRunError(
                "DIGEST_RUN_NOT_PAUSED",
                "Digest run is neither waiting_user nor stale.",
            )
        open_stages = _open_stages(selected)
        if waiting and open_stages:
            raise DigestRunError(
                "DIGEST_RUN_STAGE_OPEN",
                "Cannot resume a run with open stages: " + ", ".join(open_stages),
            )
        last_time = _parse_time(lifecycle[-1].get("timestamp"))
        if last_time is None or current < last_time:
            raise DigestRunError(
                "DIGEST_RUN_TIME_INVALID", "Digest run timestamps are invalid."
            )
        stage = _current_stage(selected)
        event = {
            "schema_version": RUN_EVENT_SCHEMA,
            "timestamp": _iso(current),
            "run_id": run_id,
            "event": "resumed",
            "stage": stage,
            "action": STAGE_ACTIONS[stage],
            "status": "running",
            "source_type": _latest_source_type(selected),
            "source_ref_hash": selected[0].get("source_ref_hash", ""),
            "detail_code": "DIGEST_RUN_RESUMED",
            "resume_from": "waiting_user" if waiting else "stale",
            "duration_ms": _active_duration_ms(selected, now=current),
            "metrics": {},
        }
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def record_heartbeat(
    kb: Path,
    *,
    run_id: str,
    stage: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if stage not in STAGE_ACTIONS:
        raise DigestRunError("DIGEST_RUN_INVALID", "Unknown digest stage.")
    current = now or _utc_now()
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        selected = _require_active_run(events, run_id, now=current)
        if stage not in _open_stages(selected):
            raise DigestRunError(
                "DIGEST_RUN_STAGE_NOT_STARTED",
                f"Heartbeat stage has no open start event: {stage}",
            )
        event = {
            "schema_version": RUN_EVENT_SCHEMA,
            "timestamp": _iso(current),
            "run_id": run_id,
            "event": "heartbeat",
            "stage": stage,
            "action": STAGE_ACTIONS[stage],
            "status": "running",
            "source_type": _latest_source_type(selected),
            "source_ref_hash": selected[0].get("source_ref_hash", ""),
            "detail_code": "DIGEST_RUN_HEARTBEAT",
            "duration_ms": _active_duration_ms(selected, now=current),
            "metrics": {},
        }
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def finish_run(
    kb: Path,
    *,
    run_id: str,
    status: str,
    error_code: str = "",
    metrics: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if status not in RUN_STATUSES:
        raise DigestRunError(
            "DIGEST_RUN_INVALID",
            "Digest run status must be committed, noop, failed, or cancelled.",
        )
    if status in {"failed", "cancelled"} and not error_code:
        raise DigestRunError(
            "DIGEST_RUN_INVALID", "Failed or cancelled runs require error_code."
        )
    _validate_detail_code(error_code)
    metric_values = _validate_metrics(metrics)
    current = now or _utc_now()
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        selected = _require_active_run(
            events,
            run_id,
            now=current,
            allow_waiting=status in {"failed", "cancelled"},
        )
        open_stages = _open_stages(selected)
        if open_stages:
            raise DigestRunError(
                "DIGEST_RUN_STAGE_OPEN",
                "Digest run has open stages: " + ", ".join(open_stages),
            )
        started_at = _parse_time(selected[0].get("timestamp"))
        if started_at is None or current < started_at:
            raise DigestRunError(
                "DIGEST_RUN_TIME_INVALID", "Digest run timestamps are invalid."
            )
        event_name = "completed" if status in {"committed", "noop"} else status
        event = {
            "schema_version": RUN_EVENT_SCHEMA,
            "timestamp": _iso(current),
            "run_id": run_id,
            "event": event_name,
            "stage": "finalize",
            "action": STAGE_ACTIONS["finalize"],
            "status": status,
            "source_type": _latest_source_type(selected),
            "source_ref_hash": selected[0].get("source_ref_hash", ""),
            "detail_code": error_code or "DIGEST_RUN_COMPLETED",
            "duration_ms": _active_duration_ms(selected, now=current),
            "metrics": metric_values,
        }
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def record_usage(
    kb: Path,
    *,
    run_id: str,
    stage: str,
    worker_role: str,
    usage_source: str,
    call_id: str,
    usage: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one privacy-safe model usage receipt to a digest run."""

    _validate_run_id(run_id)
    if stage not in STAGE_ACTIONS:
        raise DigestRunError("DIGEST_RUN_USAGE_INVALID", "Unknown usage stage.")
    if worker_role not in WORKER_ROLES:
        raise DigestRunError("DIGEST_RUN_USAGE_INVALID", "Unknown worker role.")
    if usage_source not in USAGE_SOURCES:
        raise DigestRunError("DIGEST_RUN_USAGE_INVALID", "Unknown usage source.")
    token_values = _validate_usage(usage)
    call_hash = _call_id_hash(call_id)
    current = now or _utc_now()
    with _log_lock(kb) as root:
        events = _read_events_from_root(root)
        selected = _run_events(events, run_id)
        if not selected:
            raise DigestRunError(
                "DIGEST_RUN_NOT_FOUND", f"Digest run not found: {run_id}"
            )
        duplicate = next(
            (
                event
                for event in selected
                if event.get("event") == "usage_recorded"
                and event.get("call_id_hash") == call_hash
            ),
            None,
        )
        if duplicate is not None:
            return {**duplicate, "deduplicated": True}
        event = {
            "schema_version": RUN_EVENT_SCHEMA,
            "timestamp": _iso(current),
            "run_id": run_id,
            "event": "usage_recorded",
            "stage": stage,
            "action": STAGE_ACTIONS[stage],
            "worker_role": worker_role,
            "usage_source": usage_source,
            "call_id_hash": call_hash,
            "usage": token_values,
        }
        log_path = _append(root, event, now=current)
    return {**event, "deduplicated": False, "log_path": log_path}


def _usage_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {field: 0 for field in sorted(USAGE_FIELDS)}
    by_stage: dict[str, dict[str, Any]] = {}
    source_counts = {source: 0 for source in sorted(USAGE_SOURCES)}
    model_calls = 0
    for event in events:
        if event.get("event") != "usage_recorded":
            continue
        usage = event.get("usage")
        if not isinstance(usage, Mapping):
            continue
        stage = str(event.get("stage", ""))
        stage_summary = by_stage.setdefault(
            stage,
            {
                "model_calls": 0,
                "measured_calls": 0,
                "estimated_calls": 0,
                **{field: 0 for field in sorted(USAGE_FIELDS)},
            },
        )
        model_calls += 1
        stage_summary["model_calls"] += 1
        source = str(event.get("usage_source", ""))
        if source in source_counts:
            source_counts[source] += 1
            stage_summary[source + "_calls"] += 1
        for field in USAGE_FIELDS:
            value = int(usage.get(field, 0))
            totals[field] += value
            stage_summary[field] += value
    # Providers commonly report reasoning as a subset of output tokens.
    total_tokens = totals["input_tokens"] + totals["output_tokens"]
    return {
        "model_calls": model_calls,
        "measured_calls": source_counts["measured"],
        "estimated_calls": source_counts["estimated"],
        **totals,
        "total_tokens": total_tokens,
        "by_stage": {stage: by_stage[stage] for stage in sorted(by_stage)},
    }


def _summary(events: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    first = events[0]
    lifecycle_events = _lifecycle_events(events)
    last = lifecycle_events[-1]
    lifecycle_updated_at = _parse_time(last.get("timestamp"))
    duration_ms = _active_duration_ms(events, now=now)
    stages = [
        {
            "stage": event.get("stage"),
            "action": event.get("action"),
            "status": event.get("status"),
            "duration_ms": event.get("duration_ms", 0),
            "detail_code": event.get("detail_code", ""),
            "metrics": event.get("metrics", {}),
        }
        for event in events
        if event.get("event") in {"stage_completed", "stage_failed"}
    ]
    slowest = max(stages, key=lambda item: int(item["duration_ms"]), default=None)
    terminal = _is_terminal(last)
    waiting = _is_waiting(lifecycle_events)
    stale = _is_stale(lifecycle_events, now=now)
    if terminal:
        status = str(last.get("status"))
    elif waiting:
        status = "waiting_user"
    elif stale:
        status = "stale"
    else:
        status = "running"
    stale_since = ""
    if stale and lifecycle_updated_at is not None:
        stale_since = _iso(lifecycle_updated_at + DEFAULT_STALE_AFTER)
    return {
        "run_id": first.get("run_id"),
        "source_type": _latest_source_type(events),
        "source_ref_hash": first.get("source_ref_hash"),
        "started_at": first.get("timestamp"),
        "updated_at": last.get("timestamp"),
        "usage_updated_at": (
            events[-1].get("timestamp")
            if events[-1].get("event") == "usage_recorded"
            else ""
        ),
        "status": status,
        "current_stage": _current_stage(events),
        "waiting_since": last.get("timestamp") if waiting else "",
        "waiting_reason_code": last.get("detail_code", "") if waiting else "",
        "stale_since": stale_since,
        "stale_after_seconds": round(DEFAULT_STALE_AFTER.total_seconds()),
        "duration_ms": duration_ms,
        "stage_count": len(stages),
        "slowest_stage": slowest,
        "stages": stages,
        "usage": _usage_summary(events),
    }


def list_runs(
    kb: Path,
    *,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_EVENTS_PER_QUERY:
        raise DigestRunError("DIGEST_RUN_INVALID", "limit must be between 1 and 1000.")
    current = now or _utc_now()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in _read_events(kb):
        grouped.setdefault(str(event.get("run_id", "")), []).append(event)
    summaries = [_summary(events, now=current) for events in grouped.values()]
    summaries.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return {
        "count": len(summaries),
        "returned": min(limit, len(summaries)),
        "runs": summaries[:limit],
    }


def show_run(
    kb: Path,
    *,
    run_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    selected = _run_events(_read_events(kb), _validate_run_id(run_id))
    if not selected:
        raise DigestRunError("DIGEST_RUN_NOT_FOUND", f"Digest run not found: {run_id}")
    return {
        "summary": _summary(selected, now=now or _utc_now()),
        "event_count": len(selected),
        "events": selected,
    }
