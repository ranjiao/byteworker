"""Bounded local technical receipts shared by support and the scheduled runner."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
TRACE_ENV = "BYTEWORKER_TRACE_ID"
TRACE_ID = re.compile(r"(?:BW|DT)-[0-9a-f]{32}")
DREAMING_RUN_ID = re.compile(r"DR-[0-9a-f]{32}")
DIGEST_RUN_ID = re.compile(r"DG-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")
KINDS = {
    "dreaming", "update", "thinking", "context", "search", "brief",
    "dashboard", "report", "viewer", "install", "source", "doctor",
}
JOBS = {"process", "morning", "daily", "weekly", "maintenance", "recovery"}
RESULTS = {
    "started", "running", "success", "failed", "partial", "attention", "interrupted",
    "valid", "committed", "unchanged", "unknown", "no_match", "matched",
    "insufficient_evidence", "incorrect_result", "disabled", "idle", "busy",
    "leased", "complete", "not_due", "waiting_for_user", "backoff", "blocked_by_dependency",
    "due", "selected", "not_selected", "delivered", "pending", "skipped",
    "not_recorded", "rolled_back", "rollback_failed",
}
CODES = {
    "KB_CONFIG_MISSING", "KB_CONFIG_INVALID", "KB_DIRECTORY_MISSING",
    "RUNTIME_DEPENDENCY_INVALID", "CONTEXT_MISSING",
    "REPORT_AUTOMATION_STATE_INVALID", "REPORT_AUTOMATION_LEASE_MISMATCH",
    "KB_MUTATION_BASE_MISMATCH", "KB_MUTATION_GIT_DIRTY", "KB_MUTATION_GIT_ERROR",
    "KB_MUTATION_INDEX_ERROR", "KB_MUTATION_ROLLBACK_ERROR", "KB_MUTATION_PLAN_INVALID",
    "SOURCE_AUTH_REQUIRED", "SOURCE_PERMISSION_DENIED", "SOURCE_PROFILE_INVALID",
    "DASHBOARD_READ_FAILED", "DIGEST_FLOW_COMMIT_FAILED", "DREAMING_LEASE_EXPIRED", "DREAMING_STATE_INVALID",
    "DREAMING_STATE_MIGRATION_FAILED", "DREAMING_LEASE_MISMATCH",
    "DREAMING_RUN_LOG_INVALID", "DREAMING_DIGEST_INCOMPLETE",
    "DREAMING_DELIVERY_FAILED", "DREAMING_DELIVERY_RUNTIME_MISSING",
    "DREAMING_DELIVERY_ARTIFACT_MISSING", "DREAMING_DELIVERY_TIMEOUT",
    "DREAMING_GRANT_REQUIRED", "DREAMING_KB_INVALID", "PREFLIGHT_FAILED",
    "RUN_DUE_FAILED", "LOG_UNAVAILABLE", "UNCLASSIFIED_FAILURE",
    "VIEWER_ACCESS_DENIED", "VIEWER_REQUEST_FAILED",
}
COMMANDS = {
    "kb-query": {"search", "conflict-search", "evidence", "source-record"},
    "kb-mutate": {"validate", "execute", "migrate-dashboard"},
    "context": {"view"}, "dashboard": {"view", "inputs"},
    "report-automation": {"status", "check", "lease", "complete"},
    "source": {"auth-status", "inspect", "capture", "register", "profile-save", "diff"},
    "doctor": {"scan", "fix"},
}
STAGES = {
    "request", "agent_result", "preflight", "scheduler", "job_decision", "viewer",
    "scheduled", "collection", "analysis", "consolidation", "action", "report",
    "maintenance", "recovery", "complete", "delivery",
} | {f"{tool}.{operation}" for tool, operations in COMMANDS.items() for operation in operations}
MAX_TRACES = 4096
MAX_TRACE_BYTES = 96 * 1024
RETENTION_SECONDS = 30 * 86400


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _directory() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state").expanduser()
    return state / "byteworker" / "diagnostics"


def scope(kb: Path | None) -> str:
    return hashlib.sha256(os.fsencode(kb.expanduser().resolve())).hexdigest() if kb else "local"


@contextmanager
def _locked(kb: Path | None = None):
    root = _directory()
    if root.is_symlink() or any(root.resolve().is_relative_to(path.resolve()) for path in (ROOT, kb) if path is not None):
        raise ValueError("诊断存储必须位于仓库和知识库之外。")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root


def _paths(root: Path):
    return (
        path for path in root.glob("*.json")
        if TRACE_ID.fullmatch(path.stem) and not path.is_symlink() and path.is_file()
    )


def _dated_paths(root: Path) -> list[tuple[float, Path]]:
    dated = []
    if not root.is_symlink():
        for path in _paths(root):
            try:
                dated.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                continue
    return sorted(dated, reverse=True)


def _read(path: Path) -> dict | None:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_TRACE_BYTES:
            return None
        value = json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _save(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".trace-")
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(value, separators=(",", ":")).encode())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def start(kind: str, kb: Path | None = None) -> str:
    if kind not in KINDS:
        raise ValueError("未知的诊断类型。")
    trace_id = ("DT-" if kind == "dreaming" else "BW-") + uuid4().hex
    with _locked(kb) as root:
        paths = _dated_paths(root)
        cutoff = time.time() - RETENTION_SECONDS
        for index, (modified, path) in enumerate(paths):
            if modified < cutoff or index >= MAX_TRACES - 1:
                path.unlink(missing_ok=True)
        _save(root / f"{trace_id}.json", {
            "scope": scope(kb), "kind": kind, "events": [
                {"timestamp": utc_now(), "stage": "request", "status": "started"},
            ],
        })
    return trace_id


def project_event(raw: dict) -> dict | None:
    stage, status, timestamp = (raw.get(field) for field in ("stage", "status", "timestamp"))
    if not all(isinstance(value, str) for value in (stage, status, timestamp)):
        return None
    if stage not in STAGES or status not in RESULTS:
        return None
    try:
        at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if at.tzinfo is None:
            return None
        normalized = at.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None
    event = {"timestamp": normalized, "stage": stage, "status": status}
    for field, allowed in (("code", CODES), ("job", JOBS)):
        value = raw.get(field)
        if isinstance(value, str) and value in allowed:
            event[field] = value
    duration = raw.get("duration_ms")
    if type(duration) is int and 0 <= duration < 2**53:
        event["duration_ms"] = duration
    for field, pattern in (("run_id", DREAMING_RUN_ID), ("digest_run_id", DIGEST_RUN_ID)):
        value = raw.get(field)
        if isinstance(value, str) and pattern.fullmatch(value):
            event[field] = value
    return event


def record(trace_id: str, stage: str, status: str, **fields) -> None:
    if not TRACE_ID.fullmatch(trace_id):
        raise ValueError("诊断编号无效。")
    event = project_event({"timestamp": utc_now(), "stage": stage, "status": status, **fields})
    if event is None:
        raise ValueError("诊断阶段或状态无效。")
    with _locked() as root:
        path = root / f"{trace_id}.json"
        value = _read(path)
        if value is None:
            raise ValueError("找不到该诊断编号。")
        events = value["events"]
        events.append(event)
        if len(events) > 200:
            value["events"] = events[:1] + events[-199:]
            value["truncated"] = True
        _save(path, value)


def read(trace_id: str, *, kb: Path | None = None) -> dict | None:
    if not TRACE_ID.fullmatch(trace_id):
        raise ValueError("诊断编号无效。")
    root = _directory()
    value = None if root.is_symlink() else _read(root / f"{trace_id}.json")
    if value is None or (kb is not None and value.get("scope") != scope(kb)):
        return None
    kind, events = value.get("kind"), value.get("events")
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(events, list):
        return None
    timeline = [
        event for raw in events
        if isinstance(raw, dict) and (event := project_event(raw)) is not None
    ]
    return {
        "run_id": trace_id, "kind": kind, "events": timeline,
        "truncated": value.get("truncated") is True,
        "invalid_events": len(timeline) != len(events),
    }


def recent(*, kind: str, kb: Path | None = None, limit: int = 10, linked_run_id: str = "") -> list[dict]:
    if kind not in KINDS or not 1 <= limit <= 50:
        raise ValueError("诊断类型或数量无效。")
    prefix = "DT-" if kind == "dreaming" else "BW-"
    found = []
    for _, path in _dated_paths(_directory()):
        if not path.stem.startswith(prefix):
            continue
        item = read(path.stem, kb=kb)
        if item is not None and item["kind"] == kind:
            if linked_run_id and not any(event.get("run_id") == linked_run_id for event in item["events"]):
                continue
            found.append(item)
            if len(found) == limit:
                break
    return found


def record_result(trace_id: str, tool: str, operation: str, payload: dict, elapsed: int) -> None:
    if operation not in COMMANDS.get(tool, ()):
        return
    status = payload["status"]
    data = payload.get("data")
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if status == "error":
        status = "rollback_failed" if code == "KB_MUTATION_ROLLBACK_ERROR" else "failed"
    if status == "success" and isinstance(data, dict):
        transaction = data.get("transaction")
        outcome = transaction.get("status") if isinstance(transaction, dict) else data.get("status")
        if outcome in ("committed", "unchanged", "valid", "rolled_back"):
            status = outcome
        if tool == "kb-query":
            field = {"search": "candidates", "source-record": "matches", "evidence": "evidence"}.get(operation)
            if field and isinstance(data.get(field), list):
                status = "matched" if data[field] else "no_match"
        if tool == "report-automation" and operation in {"check", "lease"} and isinstance(outcome, str) and outcome in RESULTS:
            status = outcome
        if tool == "report-automation" and operation == "lease" and "token" in data:
            status = "leased"
        if tool == "report-automation" and operation == "complete" and outcome == "failed":
            status, code = "failed", data.get("error_code")
    if status in {"failed", "rollback_failed"} and (not isinstance(code, str) or code not in CODES):
        code = "UNCLASSIFIED_FAILURE"
    record(trace_id, f"{tool}.{operation}", status, code=code, duration_ms=elapsed)
