"""Private, structured operational logs for Dreaming runs."""

from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from dreaming_state import (
    DIRECTORY_MODE,
    FILE_MODE,
    DreamingError,
    _secure_chmod,
    _secure_fchmod,
    parse_time,
    secure_path,
    utc_iso,
)


RUN_EVENT_SCHEMA = "byteworker-dreaming-run-event/v1"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_RETENTION_DAYS = 365
MAX_EVENTS_PER_QUERY = 1000
EVENT_KINDS = {
    "leased",
    "heartbeat",
    "renewed",
    "completed",
    "lease_expired",
}
STAGES = {
    "scheduled",
    "collection",
    "analysis",
    "consolidation",
    "action",
    "report",
    "maintenance",
    "recovery",
    "complete",
}
MACHINE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
BATCH_ID = re.compile(r"^EB-[0-9a-f]{32}$")


def logging_config(
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, int]:
    if retention_days < 1 or retention_days > MAX_RETENTION_DAYS:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            f"log_retention_days 必须是 1..{MAX_RETENTION_DAYS}。",
        )
    if max_file_bytes < 1024:
        raise DreamingError(
            "DREAMING_CONFIG_INVALID",
            "log max_file_bytes 不能小于 1024。",
        )
    return {
        "retention_days": retention_days,
        "max_file_bytes": max_file_bytes,
    }


def _log_root(kb: Path) -> Path:
    return secure_path(kb, "run-logs")


def _ensure_log_root(kb: Path) -> Path:
    root = _log_root(kb)
    if root.is_symlink():
        raise DreamingError(
            "DREAMING_STATE_PATH_INVALID",
            "Dreaming run-logs 目录不能是符号链接。",
        )
    root.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    _secure_chmod(root, DIRECTORY_MODE)
    return root


@contextmanager
def _log_lock(kb: Path) -> Iterator[Path]:
    root = _ensure_log_root(kb)
    lock = root / ".lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, FILE_MODE)
    _secure_chmod(lock, FILE_MODE)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield root
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _bounded(value: object, limit: int = 512) -> str:
    normalized = " ".join(str(value or "").split())
    return normalized[:limit]


def _validate_metrics(value: Mapping[str, Any] | None) -> dict[str, int]:
    if value is None:
        return {}
    allowed = {
        "duration_ms",
        "item_count",
        "finding_count",
        "gap_count",
        "progress_current",
        "progress_total",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DreamingError(
            "DREAMING_RUN_LOG_INVALID",
            "run log metrics 含未知字段: " + ", ".join(unknown),
        )
    result: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(raw, int) or raw < 0:
            raise DreamingError(
                "DREAMING_RUN_LOG_INVALID",
                f"run log metric {key} 必须是非负整数。",
            )
        result[key] = raw
    return result


def _event(
    *,
    run_id: str,
    event: str,
    job: str,
    period: str,
    owner: str,
    epoch: int,
    stage: str,
    status: str,
    error_code: str,
    artifact_path: str,
    detail_code: str,
    batch_id: str,
    metrics: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    if not run_id.strip() or event not in EVENT_KINDS:
        raise DreamingError("DREAMING_RUN_LOG_INVALID", "run_id 或 event 无效。")
    if stage not in STAGES:
        raise DreamingError(
            "DREAMING_RUN_LOG_INVALID",
            f"未知 run stage: {stage}",
        )
    if detail_code and not MACHINE_CODE.fullmatch(detail_code):
        raise DreamingError(
            "DREAMING_RUN_LOG_INVALID",
            "detail_code 必须是大写稳定机器码。",
        )
    if error_code and not MACHINE_CODE.fullmatch(error_code):
        raise DreamingError(
            "DREAMING_RUN_LOG_INVALID",
            "error_code 必须是大写稳定机器码。",
        )
    if batch_id and not BATCH_ID.fullmatch(batch_id):
        raise DreamingError(
            "DREAMING_RUN_LOG_INVALID",
            "batch_id 必须是稳定 EvidenceBatch id。",
        )
    return {
        "schema_version": RUN_EVENT_SCHEMA,
        "timestamp": utc_iso(now),
        "run_id": _bounded(run_id, 96),
        "event": event,
        "job": _bounded(job, 32),
        "period": _bounded(period, 128),
        "owner": _bounded(owner, 128),
        "epoch": int(epoch),
        "stage": stage,
        "status": _bounded(status, 32),
        "error_code": _bounded(error_code, 128),
        "artifact_path": _bounded(artifact_path, 512),
        "detail_code": _bounded(detail_code, 128),
        "batch_id": batch_id,
        "metrics": _validate_metrics(metrics),
    }


def _log_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.glob("*.jsonl"):
        if path.is_symlink():
            raise DreamingError(
                "DREAMING_STATE_PATH_INVALID",
                f"Dreaming run log 不能是符号链接: {path}",
            )
        if path.is_file():
            result.append(path)
    return sorted(result)


def _prune(root: Path, *, retention_days: int, now: datetime) -> None:
    cutoff = now.astimezone(timezone.utc).date() - timedelta(days=retention_days)
    for path in _log_files(root):
        prefix = path.name[:10]
        try:
            day = datetime.strptime(prefix, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()


def _target(root: Path, *, now: datetime, max_file_bytes: int, size: int) -> Path:
    prefix = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    index = 0
    while True:
        suffix = "" if index == 0 else f"-{index:04d}"
        candidate = root / f"{prefix}{suffix}.jsonl"
        current_size = candidate.stat().st_size if candidate.exists() else 0
        if current_size + size <= max_file_bytes:
            return candidate
        index += 1


def append_run_event(
    kb: Path,
    *,
    run_id: str,
    event: str,
    job: str,
    period: str,
    owner: str,
    epoch: int,
    stage: str,
    status: str,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    error_code: str = "",
    artifact_path: str = "",
    detail_code: str = "",
    batch_id: str = "",
    metrics: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    config = logging_config(
        retention_days=retention_days,
        max_file_bytes=max_file_bytes,
    )
    value = _event(
        run_id=run_id,
        event=event,
        job=job,
        period=period,
        owner=owner,
        epoch=epoch,
        stage=stage,
        status=status,
        error_code=error_code,
        artifact_path=artifact_path,
        detail_code=detail_code,
        batch_id=batch_id,
        metrics=metrics,
        now=current,
    )
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    with _log_lock(kb) as root:
        _prune(root, retention_days=config["retention_days"], now=current)
        target = _target(
            root,
            now=current,
            max_file_bytes=config["max_file_bytes"],
            size=len(payload),
        )
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT,
            FILE_MODE,
        )
        try:
            _secure_fchmod(descriptor, FILE_MODE)
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return value


def _read_events(kb: Path) -> list[dict[str, Any]]:
    root = _log_root(kb)
    if not root.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in _log_files(root):
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DreamingError(
                "DREAMING_RUN_LOG_INVALID",
                f"无法读取 Dreaming run log: {path.name}",
            ) from exc
        lines = content.splitlines()
        for index, line in enumerate(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                if index == len(lines) - 1 and not content.endswith("\n"):
                    continue
                raise DreamingError(
                    "DREAMING_RUN_LOG_INVALID",
                    f"Dreaming run log JSON 损坏: {path.name}",
                ) from exc
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != RUN_EVENT_SCHEMA
            ):
                raise DreamingError(
                    "DREAMING_RUN_LOG_INVALID",
                    f"Dreaming run log schema 无效: {path.name}",
                )
            events.append(value)
    events.sort(key=lambda item: str(item.get("timestamp", "")))
    return events


def list_runs(
    kb: Path,
    *,
    limit: int = 50,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_EVENTS_PER_QUERY:
        raise DreamingError("DREAMING_RUN_LOG_INVALID", "limit 必须是 1..1000。")
    if since is not None and until is not None and since > until:
        raise DreamingError("DREAMING_RUN_LOG_INVALID", "since 不能晚于 until。")
    summaries: dict[str, dict[str, Any]] = {}
    for event in _read_events(kb):
        timestamp = parse_time(event.get("timestamp"))
        if since is not None and (timestamp is None or timestamp < since):
            continue
        if until is not None and (timestamp is None or timestamp > until):
            continue
        run_id = str(event.get("run_id", ""))
        current = summaries.setdefault(
            run_id,
            {
                "run_id": run_id,
                "job": event.get("job"),
                "period": event.get("period"),
                "owner": event.get("owner"),
                "started_at": event.get("timestamp"),
                "updated_at": event.get("timestamp"),
                "stage": event.get("stage"),
                "status": event.get("status"),
                "error_code": event.get("error_code"),
                "batch_id": event.get("batch_id"),
                "event_count": 0,
            },
        )
        current.update(
            {
                "updated_at": event.get("timestamp"),
                "stage": event.get("stage"),
                "status": event.get("status"),
                "error_code": event.get("error_code"),
                "batch_id": event.get("batch_id") or current.get("batch_id"),
            }
        )
        current["event_count"] += 1
    ordered = sorted(
        summaries.values(),
        key=lambda item: str(item.get("updated_at", "")),
        reverse=True,
    )
    return {"count": len(ordered), "returned": min(limit, len(ordered)), "runs": ordered[:limit]}


def show_run(kb: Path, *, run_id: str) -> dict[str, Any]:
    events = [item for item in _read_events(kb) if item.get("run_id") == run_id]
    if not events:
        raise DreamingError("DREAMING_RUN_NOT_FOUND", f"未找到 run: {run_id}")
    return {"run_id": run_id, "event_count": len(events), "events": events}


def tail_events(
    kb: Path,
    *,
    limit: int = 50,
    run_id: str = "",
) -> dict[str, Any]:
    if limit < 1 or limit > MAX_EVENTS_PER_QUERY:
        raise DreamingError("DREAMING_RUN_LOG_INVALID", "limit 必须是 1..1000。")
    events = _read_events(kb)
    if run_id:
        events = [item for item in events if item.get("run_id") == run_id]
    selected = events[-limit:]
    return {
        "run_id": run_id or None,
        "count": len(events),
        "returned": len(selected),
        "events": selected,
    }
