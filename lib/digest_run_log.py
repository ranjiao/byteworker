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
MAX_EVENTS_PER_QUERY = 1000
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
RUN_ID_RE = re.compile(r"^DG-\d{8}T\d{6}Z-[0-9a-f]{8}$")
MACHINE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")

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


def _require_active_run(events: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    selected = _run_events(events, _validate_run_id(run_id))
    if not selected:
        raise DigestRunError("DIGEST_RUN_NOT_FOUND", f"Digest run not found: {run_id}")
    if selected[-1].get("event") in {"completed", "failed", "cancelled"}:
        raise DigestRunError(
            "DIGEST_RUN_TERMINAL",
            f"Digest run is already terminal: {run_id}",
        )
    return selected


def _open_stages(events: list[dict[str, Any]]) -> list[str]:
    open_stages: set[str] = set()
    for event in events:
        stage = str(event.get("stage", ""))
        if event.get("event") == "stage_started":
            open_stages.add(stage)
        elif event.get("event") in {"stage_completed", "stage_failed"}:
            open_stages.discard(stage)
    return sorted(open_stages)


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
        selected = _require_active_run(events, run_id)
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
            duration_ms = round((current - started_at).total_seconds() * 1000)
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
        selected = _require_active_run(events, run_id)
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
            "duration_ms": round((current - started_at).total_seconds() * 1000),
            "metrics": metric_values,
        }
        log_path = _append(root, event, now=current)
    return {**event, "log_path": log_path}


def _summary(events: list[dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    first = events[0]
    last = events[-1]
    started_at = _parse_time(first.get("timestamp"))
    updated_at = _parse_time(last.get("timestamp"))
    duration_ms = 0
    if started_at is not None:
        end = updated_at if last.get("event") in {"completed", "failed", "cancelled"} else now
        if end is not None and end >= started_at:
            duration_ms = round((end - started_at).total_seconds() * 1000)
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
    terminal = last.get("event") in {"completed", "failed", "cancelled"}
    return {
        "run_id": first.get("run_id"),
        "source_type": _latest_source_type(events),
        "source_ref_hash": first.get("source_ref_hash"),
        "started_at": first.get("timestamp"),
        "updated_at": last.get("timestamp"),
        "status": last.get("status") if terminal else "running",
        "current_stage": last.get("stage"),
        "duration_ms": duration_ms,
        "stage_count": len(stages),
        "slowest_stage": slowest,
        "stages": stages,
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
