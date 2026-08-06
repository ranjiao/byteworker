"""Complete a Dreaming report run and perform configured delivery."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dreaming_delivery_lark import deliver_lark_bot_summary
from dreaming_report_bundle import render_report_bundle
from dreaming_reports import enqueue_delivery
from dreaming_scheduler import complete_run, status
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
) -> dict[str, Any]:
    rendered = render_report_bundle(kb, document=document)
    run = complete_run(
        kb,
        token=token,
        run_status="success",
        artifact_path=str(rendered["report_path"]),
        item_count=item_count,
        finding_count=finding_count,
        gap_count=gap_count,
    )
    delivery = _deliver_if_enabled(
        kb,
        kind=str(rendered["kind"]),
        period=str(rendered["period"]),
        report_path=str(rendered["report_path"]),
        run_id=str(run["run_id"]),
        binary=delivery_binary,
    )
    return {
        "status": "completed",
        "kind": rendered["kind"],
        "period": rendered["period"],
        "run": run,
        "rendered": rendered,
        "delivery": delivery,
    }


def _deliver_if_enabled(
    kb: Path,
    *,
    kind: str,
    period: str,
    report_path: str,
    run_id: str,
    binary: str | None,
) -> dict[str, Any]:
    current = status(kb)
    lark = current.get("report_delivery", {}).get("lark_bot", {})
    if not isinstance(lark, Mapping) or not lark.get("enabled"):
        return {"status": "skipped", "reason": "lark_delivery_disabled"}
    recipient_id = str(lark.get("recipient_id", ""))
    if not recipient_id.startswith("ou_"):
        return {"status": "skipped", "reason": "lark_recipient_missing"}
    queued = enqueue_delivery(
        kb,
        kind=kind,
        period=period,
        report_path=report_path,
        commit=run_id,
        channel="lark_bot",
        artifact="summary",
        recipient_id=recipient_id,
    )
    try:
        delivered = deliver_lark_bot_summary(
            kb,
            outbox_id=str(queued["outbox_id"]),
            binary=binary,
        )
    except DreamingError as exc:
        return {
            "status": "pending",
            "outbox_id": queued["outbox_id"],
            "error": exc.as_dict(),
        }
    return {
        "status": "delivered",
        "outbox_id": queued["outbox_id"],
        "delivery_id": delivered.get("delivery_id", ""),
    }
