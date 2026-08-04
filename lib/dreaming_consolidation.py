"""Idempotent Finding history and current projection."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_state import (
    DreamingError,
    atomic_write_json,
    load_state_unlocked,
    secure_path,
    state_lock,
    utc_iso,
)


FINDINGS_SCHEMA = "byteworker-findings/v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _history_path(kb: Path) -> Path:
    return secure_path(kb, "finding-history.jsonl")


def _projection_path(kb: Path) -> Path:
    return secure_path(kb, "findings.json")


def _event_id(batch_id: str, finding_id: str) -> str:
    digest = hashlib.sha256(f"{batch_id}:{finding_id}".encode("utf-8")).hexdigest()
    return f"FE-{digest}"


def _read_history(kb: Path) -> list[dict[str, Any]]:
    path = _history_path(kb)
    if not path.is_file():
        return []
    result = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or value.get("operation") not in {
                "upsert",
                "feedback",
            }:
                raise ValueError(f"line {number}")
            if value.get("operation") == "upsert" and not isinstance(
                value.get("proposal"), dict
            ):
                raise ValueError(f"line {number}")
            if value.get("operation") == "feedback" and not isinstance(
                value.get("feedback"), dict
            ):
                raise ValueError(f"line {number}")
            result.append(value)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise DreamingError(
            "DREAMING_FINDING_HISTORY_INVALID",
            f"Finding history 损坏: {path}",
        ) from exc
    return result


def _merge_finding(
    previous: Mapping[str, Any],
    proposed: Mapping[str, Any],
    *,
    batch_id: str,
    recorded_at: str,
) -> dict[str, Any]:
    evidence = sorted(
        {
            *previous.get("evidence_refs", []),
            *proposed.get("evidence_refs", []),
        }
    )
    batches = sorted({*previous.get("batch_ids", []), batch_id})
    return {
        **dict(proposed),
        "finding_id": str(proposed["finding_id"]),
        "status": str(previous.get("status", "open")),
        "revision": int(previous.get("revision", 0)) + 1,
        "evidence_refs": evidence,
        "batch_ids": batches,
        "first_seen_at": previous.get("first_seen_at") or recorded_at,
        "updated_at": recorded_at,
    }


def _projection_from_history(events: list[Mapping[str, Any]]) -> dict[str, Any]:
    findings: dict[str, Any] = {}
    applied = []
    for event in events:
        event_id = str(event.get("event_id", ""))
        proposal = event.get("proposal")
        operation = event.get("operation")
        if not event_id:
            continue
        if operation == "feedback":
            feedback = event.get("feedback")
            finding_id = str(event.get("finding_id", ""))
            current = findings.get(finding_id)
            if isinstance(feedback, Mapping) and isinstance(current, dict):
                current["status"] = str(feedback["status"])
                current["feedback"] = feedback.get("value")
                current["snooze_until"] = feedback.get("snooze_until")
                current["updated_at"] = str(event.get("recorded_at", ""))
            applied.append(event_id)
            continue
        if not isinstance(proposal, Mapping):
            continue
        finding_id = str(proposal["finding_id"])
        previous = findings.get(finding_id)
        previous = previous if isinstance(previous, Mapping) else {}
        findings[finding_id] = _merge_finding(
            previous,
            proposal,
            batch_id=str(event.get("batch_id", "")),
            recorded_at=str(event.get("recorded_at", "")),
        )
        applied.append(event_id)
    return {
        "schema_version": FINDINGS_SCHEMA,
        "findings": findings,
        "applied_events": applied,
    }


def _append_event(path: Path, event: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.chmod(path, 0o600)
    payload = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    with os.fdopen(descriptor, "ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def consolidate_findings(
    kb: Path,
    *,
    batch_id: str,
    bundle: Mapping[str, Any],
    expected_grant_revision: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        if (
            state["grants"]["revision"] != expected_grant_revision
            or not state["grants"]["im"]["persist_finding"]
        ):
            raise DreamingError(
                "DREAMING_GRANT_STALE",
                "Finding 持久化授权已变化或已关闭。",
            )
        events = _read_history(kb)
        projection = _projection_from_history(events)
        existing_event_ids = set(projection["applied_events"])
        appended = 0
        for proposed in bundle["findings"]:
            finding_id = str(proposed["finding_id"])
            event_id = _event_id(batch_id, finding_id)
            if event_id in existing_event_ids:
                continue
            previous = projection["findings"].get(finding_id)
            previous = previous if isinstance(previous, Mapping) else {}
            recorded_at = utc_iso(current)
            finding = _merge_finding(
                previous,
                proposed,
                batch_id=batch_id,
                recorded_at=recorded_at,
            )
            event = {
                "schema_version": "byteworker-finding-event/v1",
                "event_id": event_id,
                "batch_id": batch_id,
                "finding_id": finding_id,
                "operation": "upsert",
                "recorded_at": recorded_at,
                "proposal": dict(proposed),
            }
            _append_event(_history_path(kb), event)
            projection["findings"][finding_id] = finding
            projection["applied_events"].append(event_id)
            existing_event_ids.add(event_id)
            appended += 1
        projection["updated_at"] = utc_iso(current)
        atomic_write_json(_projection_path(kb), projection)
    return {
        "batch_id": batch_id,
        "persisted": True,
        "appended_events": appended,
        "finding_count": len(bundle["findings"]),
        "projection_count": len(projection["findings"]),
    }


def purge_findings_for_batches_unlocked(
    kb: Path,
    *,
    batch_ids: set[str],
    now: datetime,
) -> dict[str, int]:
    events = _read_history(kb)
    kept = [event for event in events if str(event.get("batch_id", "")) not in batch_ids]
    removed = len(events) - len(kept)
    if removed:
        history = _history_path(kb)
        temporary = history.with_name(f".{history.name}.rewrite")
        try:
            if temporary.exists():
                temporary.unlink()
            for event in kept:
                _append_event(temporary, event)
            if kept:
                os.replace(temporary, history)
                os.chmod(history, 0o600)
            else:
                history.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)
        projection = _projection_from_history(kept)
        projection["updated_at"] = utc_iso(now)
        atomic_write_json(_projection_path(kb), projection)
    return {"removed_events": removed}


def rebuild_findings_projection(
    kb: Path,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or _now()
    with state_lock(kb):
        events = _read_history(kb)
        projection = _projection_from_history(events)
        projection["updated_at"] = utc_iso(current)
        atomic_write_json(_projection_path(kb), projection)
    return {
        "events": len(events),
        "findings": len(projection["findings"]),
    }


def load_findings(kb: Path) -> dict[str, Any]:
    with state_lock(kb):
        events = _read_history(kb)
        projection = _projection_from_history(events)
    return projection


def review_findings(
    kb: Path,
    *,
    status: str = "open",
    limit: int = 50,
) -> dict[str, Any]:
    if status not in {"open", "snoozed", "resolved", "dismissed", "promoted", "all"}:
        raise DreamingError("DREAMING_FINDING_STATUS_INVALID", "finding status 非法。")
    projection = load_findings(kb)
    values = [
        finding
        for finding in projection["findings"].values()
        if status == "all" or finding.get("status") == status
    ]
    values.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    summaries = [
        {
            key: finding.get(key)
            for key in (
                "finding_id",
                "kind",
                "summary",
                "why_it_matters",
                "confidence",
                "status",
                "updated_at",
                "snooze_until",
            )
        }
        for finding in values[: max(1, min(limit, 200))]
    ]
    return {
        "status": status,
        "count": len(values),
        "returned": len(summaries),
        "findings": summaries,
    }


def explain_finding(kb: Path, *, finding_id: str) -> dict[str, Any]:
    finding = load_findings(kb)["findings"].get(finding_id)
    if not isinstance(finding, Mapping):
        raise DreamingError(
            "DREAMING_FINDING_NOT_FOUND",
            f"Finding 不存在: {finding_id}",
        )
    evidence = []
    from dreaming_analysis import load_evidence_batch

    for batch_id in finding.get("batch_ids", []):
        batch = load_evidence_batch(kb, str(batch_id))
        evidence.append(
            {
                "batch_id": batch_id,
                "manifest_path": f"state/dreaming/batches/{batch_id}/manifest.json",
                "source": batch["source"],
                "window": batch["window"],
                "coverage": batch["coverage"],
                "evidence_refs": [
                    item["item_id"]
                    for item in batch["items"]
                    if item["item_id"] in finding.get("evidence_refs", [])
                ],
            }
        )
    return {"finding": dict(finding), "evidence": evidence}


def record_finding_feedback(
    kb: Path,
    *,
    finding_id: str,
    status: str,
    value: str,
    request_id: str,
    snooze_until: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if status not in {"open", "snoozed", "resolved", "dismissed", "promoted"}:
        raise DreamingError("DREAMING_FINDING_STATUS_INVALID", "finding status 非法。")
    if value not in {
        "helpful",
        "unimportant",
        "already_known",
        "handled",
        "wrong_link",
        "none",
    }:
        raise DreamingError("DREAMING_FINDING_FEEDBACK_INVALID", "feedback value 非法。")
    if not request_id.strip():
        raise DreamingError("DREAMING_FINDING_FEEDBACK_INVALID", "request_id 不能为空。")
    current = now or _now()
    if status == "snoozed":
        from dreaming_state import parse_time

        until = parse_time(snooze_until)
        if until is None or until <= current:
            raise DreamingError(
                "DREAMING_FINDING_FEEDBACK_INVALID",
                "snoozed 必须提供未来 snooze_until。",
            )
    event_id = "FF-" + hashlib.sha256(
        f"{finding_id}:{request_id}".encode("utf-8")
    ).hexdigest()
    with state_lock(kb):
        events = _read_history(kb)
        projection = _projection_from_history(events)
        if finding_id not in projection["findings"]:
            raise DreamingError(
                "DREAMING_FINDING_NOT_FOUND",
                f"Finding 不存在: {finding_id}",
            )
        existing = next(
            (event for event in events if event.get("event_id") == event_id),
            None,
        )
        proposed_feedback = {
            "status": status,
            "value": value,
            "snooze_until": snooze_until or None,
        }
        if existing is not None and existing.get("feedback") != proposed_feedback:
            raise DreamingError(
                "DREAMING_FINDING_FEEDBACK_CONFLICT",
                "相同 request_id 已绑定不同 feedback。",
            )
        if existing is None:
            event = {
                "schema_version": "byteworker-finding-event/v1",
                "event_id": event_id,
                "finding_id": finding_id,
                "operation": "feedback",
                "recorded_at": utc_iso(current),
                "feedback": proposed_feedback,
            }
            _append_event(_history_path(kb), event)
            events.append(event)
            projection = _projection_from_history(events)
            projection["updated_at"] = utc_iso(current)
            atomic_write_json(_projection_path(kb), projection)
    return {
        "finding_id": finding_id,
        "status": projection["findings"][finding_id]["status"],
        "event_id": event_id,
    }
