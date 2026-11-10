"""Build local feedback artifacts from an export-safe Digest timeline."""

from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path

from feedback_export import make_draft, write_bundle
from digest_run_log import (
    DEFAULT_STALE_AFTER,
    RUN_EVENT_SCHEMA,
    SKILL_REVISION_RE,
    STAGE_ACTIONS,
    WAIT_REASONS,
    diagnostic_events,
    skill_revision,
)


SCHEMA = "byteworker-digest-feedback/v1"
MAX_TIMELINE_EVENTS = 1000
EVENT_STATUSES = {
    "started": {"running"},
    "stage_started": {"started"},
    "stage_completed": {"completed"},
    "stage_failed": {"failed"},
    "waiting_user": {"waiting_user"},
    "resumed": {"running"},
    "heartbeat": {"running"},
    "completed": {"committed", "noop"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
}
TERMINAL_EVENTS = {"completed", "failed", "cancelled"}
SAFE_DETAIL_CODES = WAIT_REASONS | {
    "DIGEST_RUN_STARTED",
    "DIGEST_RUN_RESUMED",
    "DIGEST_RUN_HEARTBEAT",
    "DIGEST_RUN_COMPLETED",
    "DIGEST_FLOW_START_FAILED",
    "DIGEST_FLOW_CLASSIFIED",
    "DIGEST_FLOW_CAPTURE_STARTED",
    "DIGEST_FLOW_CAPTURE_COMPLETED",
    "DIGEST_FLOW_CAPTURE_FAILED",
    "DIGEST_FLOW_BUNDLE_STARTED",
    "DIGEST_FLOW_BUNDLE_COMPLETED",
    "DIGEST_FLOW_BUNDLE_FAILED",
    "DIGEST_FLOW_PREFLIGHT_COMPLETED",
    "DIGEST_FLOW_PREFLIGHT_FAILED",
    "DIGEST_FLOW_ANALYSIS_PREPARED",
    "DIGEST_FLOW_ANALYSIS_FAILED",
    "DIGEST_FLOW_TRANSACTION_STARTED",
    "DIGEST_FLOW_TRANSACTION_COMPLETED",
    "DIGEST_FLOW_TRANSACTION_COMMITTED",
    "DIGEST_FLOW_TRANSACTION_NOOP",
    "DIGEST_FLOW_TRANSACTION_FAILED",
    "DIGEST_CAPTURE_JOB_FAILED",
    "DIGEST_CAPTURE_PLAN_INVALID",
    "SOURCE_CAPTURE_FAILED",
    "SOURCE_BUNDLE_REQUEST_INVALID",
    "SOURCE_BUNDLE_REQUEST_NOT_FOUND",
    "SOURCE_BUNDLE_REQUEST_INLINE_UNSUPPORTED",
}


def _safe_event(raw: dict) -> dict | None:
    event, stage, status, timestamp = (
        raw.get(field) for field in ("event", "stage", "status", "timestamp")
    )
    if not all(isinstance(value, str) for value in (event, stage, status, timestamp)):
        return None
    if stage not in STAGE_ACTIONS or status not in EVENT_STATUSES.get(event, ()):
        return None
    try:
        at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if at.tzinfo is None:
            return None
        at = at.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    result = {
        "event": event,
        "stage": stage,
        "status": status,
        "timestamp": at.isoformat(timespec="milliseconds"),
    }
    code = raw.get("detail_code")
    if isinstance(code, str) and code in SAFE_DETAIL_CODES:
        result["detail_code"] = code
    duration = raw.get("duration_ms")
    if type(duration) is int and 0 <= duration < 2**53:
        result["duration_ms"] = duration
    return result


def _transaction_outcome(events: list[dict]) -> str:
    for event in reversed(events):
        if event["event"] == "completed":
            return event["status"]
        if event["stage"] == "transaction" and event["event"] in {
            "stage_started", "stage_completed", "stage_failed"
        }:
            code = event.get("detail_code")
            if code == "DIGEST_FLOW_TRANSACTION_COMMITTED":
                return "committed"
            if code == "DIGEST_FLOW_TRANSACTION_NOOP":
                return "noop"
            if event["event"] == "stage_failed":
                return "failed"
            return "unknown"
    return "unknown"


def _diagnostic(kb: Path, run_id: str, skill_root: Path) -> tuple[dict, list[dict]]:
    raw_events, gaps = diagnostic_events(kb, run_id=run_id)
    timeline = []
    run_revision = None
    for raw in raw_events:
        if raw.get("event") == "usage_recorded":
            continue
        event = _safe_event(raw)
        if event is None:
            gaps.add("LOG_RECORD_INVALID")
            continue
        timeline.append(event)
        revision = raw.get("skill_revision")
        if event["event"] == "started" and isinstance(revision, str):
            if SKILL_REVISION_RE.fullmatch(revision):
                run_revision = revision
    timeline.sort(key=lambda event: event["timestamp"])
    last = timeline[-1] if timeline else None
    if not timeline or timeline[0]["event"] != "started":
        gaps.add("RUN_START_NOT_RECORDED")
    if last is None or last["event"] not in TERMINAL_EVENTS:
        gaps.add("RUN_TERMINAL_NOT_RECORDED")
    if run_revision is None:
        gaps.add("RUN_VERSION_NOT_RECORDED")

    status = "unknown"
    problem = None
    if last is not None:
        status = last["status"]
        if last["event"] == "stage_failed":
            status = "stage_failed"
        elif last["event"] not in TERMINAL_EVENTS | {"waiting_user"}:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last["timestamp"])
            status = "stale" if age > DEFAULT_STALE_AFTER else "running"
        if status in {"stage_failed", "failed", "cancelled", "waiting_user", "stale"}:
            problem = last
        if status == "failed" and len(timeline) > 1 and timeline[-2]["event"] == "stage_failed":
            problem = timeline[-2]

    reason_code = None
    if problem and problem["status"] in {"failed", "cancelled", "waiting_user"}:
        reason_code = problem.get("detail_code") or last.get("detail_code") or "UNCLASSIFIED_FAILURE"

    outcome = _transaction_outcome(timeline)
    if len(timeline) > MAX_TIMELINE_EVENTS:
        timeline = timeline[:1] + timeline[-(MAX_TIMELINE_EVENTS - 1):]
        gaps.add("TIMELINE_TRUNCATED")
    system = platform.system()
    summary = {
        "schema_version": SCHEMA,
        "run_event_schema": RUN_EVENT_SCHEMA,
        "run_id": run_id,
        "run_revision": run_revision,
        "export_revision": skill_revision(skill_root),
        "runtime": {
            "os": system if system in {"Darwin", "Linux", "Windows"} else "other",
            "python": platform.python_version(),
        },
        "status": status,
        "last_stage": last["stage"] if last else None,
        "problem_stage": problem["stage"] if problem else None,
        "reason_code": reason_code,
        "transaction_outcome": outcome,
        "diagnostic_gaps": sorted(gaps),
    }
    return summary, timeline


def feedback_draft(kb: Path, *, run_id: str, skill_root: Path) -> dict:
    summary, _ = _diagnostic(kb, run_id, skill_root)
    subject = f"[Byteworker] Digest 反馈 · {summary['status']}"
    body = "\n".join([
        f"运行编号：{run_id}",
        f"运行版本：{summary['run_revision'] or '未知'}",
        f"导出版本：{summary['export_revision'] or '未知'}",
        f"运行状态：{summary['status']}",
        f"阶段：{summary['problem_stage'] or summary['last_stage'] or '未知'}",
        f"原因码：{summary['reason_code'] or '无记录'}",
        f"事务结果：{summary['transaction_outcome']}",
        f"诊断缺口：{', '.join(summary['diagnostic_gaps']) or '无'}",
        "",
        "预期：",
        "实际：",
    ])
    return make_draft(run_id=run_id, schema=SCHEMA, subject=subject, body=body)


def export_bundle(kb: Path, *, run_id: str, skill_root: Path) -> dict:
    summary, timeline = _diagnostic(kb, run_id, skill_root)
    return write_bundle(kb, skill_root=skill_root, summary=summary, timeline=timeline)
