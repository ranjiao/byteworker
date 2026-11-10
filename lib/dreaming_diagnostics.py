"""Host wake evidence and read-only reconciliation of Dreaming runtime receipts."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import operation_trace as trace
from dreaming_run_log import show_run
from dreaming_scheduler import _job_gate, _status_value, run_due
from dreaming_state import DreamingError, parse_time, read_state_for_diagnostics


SCHEMA = "byteworker-dreaming-diagnosis/v1"
REPORTS = {"morning", "daily", "weekly"}


def run_tick(kb: Path, *, owner: str, lease_seconds: int, root: Path) -> dict:
    tick_id = trace.start("dreaming", kb)
    environment = {key: value for key, value in os.environ.items() if key != trace.TRACE_ENV}
    try:
        result = subprocess.run(
            [root / "bin/byteworker", "preflight", "--unattended", "--kb", kb],
            capture_output=True, check=False, env=environment,
        )
    except OSError:
        trace.record(tick_id, "preflight", "failed", code="PREFLIGHT_FAILED")
        return {"tick_id": tick_id, "status": "preflight_failed"}
    if result.returncode:
        try:
            notices = json.loads(result.stdout).get("notices", [])
        except (ValueError, AttributeError):
            notices = []
        if not isinstance(notices, list):
            notices = []
        codes = {
            notice.get("code") for notice in notices
            if isinstance(notice, dict) and notice.get("severity") == "blocking"
            and isinstance(notice.get("code"), str) and notice["code"] in trace.CODES
        }
        for code in sorted(codes or {"PREFLIGHT_FAILED"}):
            trace.record(tick_id, "preflight", "failed", code=code)
        return {"tick_id": tick_id, "status": "preflight_failed"}
    trace.record(tick_id, "preflight", "success")
    # The preflight may have updated the checkout. Load the scheduler in a new
    # process so its modules all come from the post-update revision.
    try:
        result = subprocess.run(
            [root / "bin/byteworker", "dreaming", "run-due", "--kb", kb, "--owner", owner,
             "--lease-seconds", str(lease_seconds), "--tick-id", tick_id],
            capture_output=True, check=False, env=environment,
        )
        response = json.loads(result.stdout)
        if result.returncode == 0 and isinstance(response.get("data"), dict):
            return {**response["data"], "tick_id": tick_id}
    except (OSError, ValueError, AttributeError):
        pass
    events = (trace.read(tick_id, kb=kb) or {}).get("events", [])
    if not any(event["stage"] == "scheduler" for event in events):
        trace.record(tick_id, "scheduler", "failed", code="RUN_DUE_FAILED")
    return {"tick_id": tick_id, "status": "scheduler_failed"}


def observed_run_due(kb: Path, *, owner: str, lease_seconds: int, followup: str, tick_id: str) -> dict:
    if not tick_id:
        return run_due(kb, owner=owner, lease_seconds=lease_seconds, followup_after_run_id=followup)
    tick = trace.read(tick_id, kb=kb)
    if not tick or tick["kind"] != "dreaming":
        raise DreamingError("DREAMING_RUN_INVALID", "找不到此知识库的唤醒记录。")
    observation: dict = {}
    try:
        result = run_due(
            kb, owner=owner, lease_seconds=lease_seconds, followup_after_run_id=followup,
            observation=observation,
        )
    except (DreamingError, OSError) as exc:
        code = getattr(exc, "code", "RUN_DUE_FAILED")
        _record_scheduler(
            tick_id, "failed", code=code if code in trace.CODES else "RUN_DUE_FAILED",
            run_id=observation.get("run_id"), job=observation.get("job"),
        )
        raise
    lease = result.get("lease")
    _record_scheduler(
        tick_id, result["status"],
        run_id=lease["run_id"] if lease else None,
        job=result.get("job"),
    )
    for job, decision in observation.get("jobs", {}).items():
        _record_scheduler(tick_id, decision, stage="job_decision", job=job)
    return result


def _record_scheduler(tick_id: str, status: str, *, stage: str = "scheduler", **fields) -> None:
    # Recording after acquisition must never suppress a valid lease response.
    try:
        trace.record(tick_id, stage, status, **fields)
    except (OSError, ValueError, KeyError, TypeError):
        pass


def _time(value: object) -> str | None:
    try:
        parsed = parse_time(value)
    except (ValueError, OverflowError):
        return None
    return parsed.isoformat() if parsed else None


def _id(value: object) -> str | None:
    return value if isinstance(value, str) and trace.DREAMING_RUN_ID.fullmatch(value) else None


def _project_attempt(value: object, now: datetime) -> dict | None:
    if not isinstance(value, dict):
        return None
    run_id = _id(value.get("run_id"))
    if run_id is None:
        return None
    result = {
        "run_id": run_id,
        "status": value.get("status") if value.get("status") in ("running", "success", "partial", "failed") else "unknown",
        "started_at": _time(value.get("started_at", value.get("acquired_at"))),
        "finished_at": _time(value.get("finished_at")),
        "last_heartbeat_at": _time(value.get("last_heartbeat_at")),
        "expires_at": _time(value.get("lease_expires_at", value.get("expires_at"))),
    }
    stage, code = value.get("stage"), value.get("error_code")
    if isinstance(stage, str) and stage in trace.STAGES:
        result["stage"] = stage
    if isinstance(code, str) and code in trace.CODES:
        result["code"] = code
    expires_at = result["expires_at"]
    if result["status"] == "running" and expires_at and datetime.fromisoformat(expires_at) <= now:
        result["status"] = "interrupted"
    return result


def _run_events(kb: Path, run_id: str, gaps: set[str]) -> list[dict]:
    try:
        raw = show_run(kb, run_id=run_id)["events"]
    except (DreamingError, OSError, ValueError):
        gaps.add("RUN_LOG_UNAVAILABLE")
        return []
    timeline = []
    for item in raw:
        event = trace.project_event({
            "timestamp": item.get("timestamp"), "stage": item.get("stage"),
            "status": item.get("status"), "code": item.get("error_code") or item.get("detail_code"),
            "job": item.get("job"), "run_id": run_id, "digest_run_id": item.get("digest_run_id"),
        })
        if event is not None:
            timeline.append(event)
        else:
            gaps.add("RUN_LOG_INCOMPLETE")
    if len(timeline) > 200:
        gaps.add("RUN_LOG_TRUNCATED")
        timeline = timeline[:1] + timeline[-199:]
    return timeline


def diagnose(kb: Path, *, job: str = "", tick_id: str = "", run_id: str = "") -> dict:
    if job and job not in trace.JOBS:
        raise ValueError("未知任务。")
    if tick_id and not (trace.TRACE_ID.fullmatch(tick_id) and tick_id.startswith("DT-")):
        raise ValueError("唤醒编号无效。")
    if run_id and _id(run_id) is None:
        raise ValueError("运行编号无效。")
    now = datetime.now(timezone.utc)
    gaps: set[str] = set()
    ticks = trace.recent(kind="dreaming", kb=kb, limit=12)
    if tick_id:
        selected_tick = trace.read(tick_id, kb=kb)
    elif run_id:
        linked = trace.recent(kind="dreaming", kb=kb, limit=1, linked_run_id=run_id)
        selected_tick = linked[0] if linked else None
    else:
        selected_tick = ticks[0] if ticks else None
    if selected_tick is None:
        gaps.add("WAKE_NOT_OBSERVED" if not (run_id or tick_id) else "WAKE_LINK_NOT_RECORDED")
    elif selected_tick["invalid_events"] or selected_tick["truncated"]:
        gaps.add("WAKE_LOG_INCOMPLETE")
    try:
        state = read_state_for_diagnostics(kb, now)
        current = _status_value(state, now)
        raw_lease = state.get("active_lease")
        active_lease = _project_attempt({**raw_lease, "status": "running"}, now) if isinstance(raw_lease, dict) else None
    except (DreamingError, OSError, ValueError, TypeError):
        current = {}
        active_lease = None
        gaps.add("SCHEDULER_STATE_UNAVAILABLE")

    jobs = {}
    for name, value in current.get("jobs", {}).items():
        if name not in trace.JOBS or not isinstance(value, dict):
            continue
        reason = _job_gate(value, now) or ("due" if value.get("due") else "not_due")
        jobs[name] = {
            "status": reason,
            "next_due_at": _time(value.get("next_due_at")),
            "next_attempt_at": _time(value.get("next_attempt_at")),
            "last_attempt": _project_attempt(value.get("last_attempt"), now),
            "last_success": _project_attempt(value.get("last_success"), now),
        }

    wake_events = selected_tick["events"] if selected_tick else []
    if not run_id:
        if job and not tick_id:
            run_id = (jobs.get(job, {}).get("last_attempt") or {}).get("run_id", "")
        else:
            run_id = next(
                (event["run_id"] for event in reversed(wake_events)
                 if event.get("run_id") and (not job or event.get("job") == job)),
                "",
            )
    attempt = next(
        (item for info in jobs.values() for item in (info["last_attempt"], info["last_success"])
         if item and item["run_id"] == run_id),
        None,
    )
    events = _run_events(kb, run_id, gaps) if run_id else []
    run_job = next(
        (name for name, info in jobs.items()
         if any(item and item["run_id"] == run_id for item in (info["last_attempt"], info["last_success"]))),
        events[0].get("job") if events else None,
    )
    if run_id and attempt and (
        not events or (attempt["status"] in {"success", "failed", "partial"} and events[-1]["status"] != attempt["status"])
    ):
        gaps.add("RUN_STATE_WITHOUT_MATCHING_LOG")
    run_status = attempt["status"] if attempt else events[-1]["status"] if events else "unknown"
    delivery = None
    delivery_at = None
    delivery_code = None
    if run_job in REPORTS:
        receipts = [
            item for item in current.get("outbox", {}).values()
            if isinstance(item, dict) and item.get("commit") == run_id
            and item.get("status") in ("pending", "delivered")
        ]
        receipt = next((item for item in receipts if item["status"] == "delivered"), receipts[-1] if receipts else {})
        delivery = receipt.get("status", "not_recorded")
        delivery_at = _time(receipt.get("delivered_at") or receipt.get("last_attempt_at") or receipt.get("created_at"))
        code = receipt.get("last_error_code")
        if delivery == "pending" and isinstance(code, str) and code in trace.CODES:
            delivery_code = code
    harness = current.get("harness", {})
    return {
        "schema_version": SCHEMA,
        "enabled": current.get("enabled"),
        "operational": current.get("operational"),
        "last_scheduler_at": _time(harness.get("last_tick_at")),
        "active_lease": active_lease,
        "jobs": jobs,
        "tick": selected_tick,
        "recent_wakes": [
            {"tick_id": tick["run_id"], "started_at": tick["events"][0]["timestamp"],
             "status": next((event["status"] for event in reversed(tick["events"])
                             if event["stage"] in {"scheduler", "preflight"}), "started")}
            for tick in ticks if tick["events"]
        ],
        "run": {"run_id": run_id, "job": run_job, "status": run_status, "events": events,
                "attempt": attempt, "delivery": delivery, "delivery_at": delivery_at,
                "delivery_code": delivery_code} if run_id else None,
        "diagnostic_gaps": sorted(gaps),
    }
