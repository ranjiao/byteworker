"""Complete a Dreaming report run and perform configured delivery."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from dreaming_delivery_lark import deliver_lark_bot_summary
from dreaming_report_bundle import render_report_bundle, validate_report_document
from dreaming_reports import enqueue_delivery_unlocked
from dreaming_run_result import RESULT_SCHEMA
from dreaming_scheduler import complete_run
from dreaming_state import DreamingError


def complete_report_run(
    kb: Path,
    *,
    token: str,
    document: Mapping[str, Any],
    item_count: int | None = None,
    finding_count: int | None = None,
    gap_count: int | None = None,
    delivery_binary: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = validate_report_document(document)
    rendered = {}
    queued = {}

    def publish(dependency: Mapping[str, Any], lease: Mapping[str, Any]) -> dict[str, Any]:
        coverage = normalized["coverage"]
        if dependency["status"] == "partial":
            coverage["status"] = "partial"
        reasons = dependency["partial_reasons"]
        daily_gaps = [item for item in reasons if item["kind"] == "daily_coverage_gap"]
        notes = []
        if any(item["source"] == "im" for item in daily_gaps):
            notes.append("本期消息覆盖不完整，仅纳入已核实的信息。")
        routine_gaps = sum(item["source"] == "routine_digest" for item in daily_gaps)
        if routine_gaps:
            notes.append(f"本期有 {routine_gaps} 个定期来源未完成覆盖，缺失信息未纳入报告。")
        if any(item["kind"] == "im_discovery_best_effort" for item in reasons):
            notes.append("全可见会话仅尽力覆盖。")
        notes.extend(dict.fromkeys(item["note"] for item in reasons if item["kind"] == "manual_source_gap"))
        coverage["notes"] = [*notes, *(note for note in coverage["notes"] if note not in notes)][:50]
        normalized["window"] = dependency["window"]
        rendered.update(render_report_bundle(kb, document=normalized))
        return rendered

    def stage_delivery(state, lease, report_path, run_id, current):
        queued.update(_stage_delivery(
            state, lease=lease, report_path=report_path, run_id=run_id, now=current,
        ))

    run = complete_run(
        kb,
        token=token,
        run_status="success",
        item_count=item_count,
        finding_count=finding_count,
        gap_count=gap_count,
        result_document={
            "schema_version": RESULT_SCHEMA, "job": normalized["kind"],
            "period": normalized["period"], "summary": normalized["message_summary"],
        },
        publish_report=publish,
        stage_report_delivery=stage_delivery,
        now=now,
    )
    delivery = queued
    if queued["status"] == "pending":
        delivery = _deliver_if_enabled(kb, outbox_id=queued["outbox_id"], binary=delivery_binary, now=now)
    return {
        "status": "completed",
        "kind": rendered["kind"],
        "period": rendered["period"],
        "run": run,
        "rendered": rendered,
        "delivery": delivery,
    }


def _stage_delivery(
    state: dict[str, Any],
    *,
    lease: Mapping[str, Any],
    report_path: str,
    run_id: str,
    now: datetime,
) -> dict[str, Any]:
    manual = lease.get("manual_report")
    if manual and not manual.get("deliver"):
        return {"status": "skipped", "reason": "manual_delivery_not_requested"}
    lark = state.get("report_delivery", {}).get("lark_bot", {})
    if not isinstance(lark, Mapping) or not lark.get("enabled"):
        return {"status": "skipped", "reason": "lark_delivery_disabled"}
    recipient_id = str(lark.get("recipient_id", ""))
    if not recipient_id.startswith("ou_"):
        return {"status": "skipped", "reason": "lark_recipient_missing"}
    return enqueue_delivery_unlocked(
        state,
        kind=str(lease["job"]),
        period=str(lease["period"]),
        report_path=report_path,
        commit=run_id,
        channel="lark_bot",
        artifact="summary",
        recipient_id=recipient_id,
        now=now,
    )


def _deliver_if_enabled(
    kb: Path, *, outbox_id: str, binary: str | None, now: datetime | None,
) -> dict[str, Any]:
    try:
        delivered = deliver_lark_bot_summary(
            kb,
            outbox_id=outbox_id,
            binary=binary,
            now=now,
        )
    except DreamingError as exc:
        return {
            "status": "pending",
            "outbox_id": outbox_id,
            "error": exc.as_dict(),
        }
    if delivered.get("status") == "skipped":
        return delivered
    return {
        "status": "delivered",
        "outbox_id": outbox_id,
        "delivery_id": delivered.get("delivery_id", ""),
    }
