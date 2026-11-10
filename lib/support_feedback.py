"""Project local operation and Dreaming diagnostics onto the support contract."""

from __future__ import annotations

from pathlib import Path

import operation_trace as trace
from digest_run_log import skill_revision
from dreaming_diagnostics import diagnose
from feedback_export import make_draft, write_bundle


SCHEMA = "byteworker-support-feedback/v1"


def _projection(kb: Path | None, run_id: str, root: Path) -> tuple[dict, list[dict]]:
    if not trace.TRACE_ID.fullmatch(run_id) and not trace.DREAMING_RUN_ID.fullmatch(run_id):
        raise ValueError("诊断编号无效。")
    if run_id.startswith(("DT-", "DR-")):
        if kb is None:
            raise ValueError("Dreaming 反馈需要指定知识库。")
        diagnosis = diagnose(
            kb, tick_id=run_id if run_id.startswith("DT-") else "",
            run_id=run_id if run_id.startswith("DR-") else "",
        )
        tick = diagnosis["tick"] or {}
        run = diagnosis["run"] or {}
        timeline = tick.get("events", []) + run.get("events", [])
        gaps = diagnosis["diagnostic_gaps"]
        delivery = run.get("delivery")
        status = run.get("status")
        if run.get("job") and delivery in ("pending", "delivered"):
            timeline.append({
                "timestamp": run.get("delivery_at"),
                "stage": "delivery", "status": delivery, "job": run["job"],
                "code": run.get("delivery_code"),
            })
        kind = "dreaming"
    else:
        request = trace.read(run_id, kb=kb)
        timeline = request["events"] if request else []
        kind = request["kind"] if request else "unknown"
        gaps = []
        if not request:
            gaps.append("TRACE_UNAVAILABLE")
        elif request["truncated"] or request["invalid_events"]:
            gaps.append("TRACE_INCOMPLETE")
        status = None
        delivery = None
    timeline = [
        projected for event in timeline
        if (projected := trace.project_event(event)) is not None
    ]
    timeline.sort(key=lambda event: event["timestamp"])
    decisions = [event for event in timeline if event["stage"] != "job_decision"]
    last = decisions[-1] if decisions else {}
    problem = next(
        (event for event in reversed(decisions) if event["status"] in ("failed", "rollback_failed", "partial")
         or (event["stage"] == "delivery" and event.get("code"))),
        {},
    )
    outcome = next(
        (event["status"] for event in reversed(decisions) if event["status"] in (
            "committed", "unchanged", "rolled_back", "rollback_failed",
        )),
        "unknown",
    )
    summary = {
        "schema_version": SCHEMA, "run_id": run_id, "kind": kind,
        "export_revision": skill_revision(root), "status": status or last.get("status", "unknown"),
        "last_stage": last.get("stage"), "reason_code": problem.get("code"),
        "transaction_outcome": outcome, "delivery": delivery, "diagnostic_gaps": gaps,
    }
    return summary, timeline


def generate(kb: Path | None, *, run_id: str, root: Path, bundle: bool) -> dict:
    summary, timeline = _projection(kb, run_id, root)
    if bundle:
        return write_bundle(kb, skill_root=root, summary=summary, timeline=timeline)
    subject = f"[Byteworker] {summary['kind']} 反馈 · {summary['status']}"
    body = "\n".join([
        f"诊断编号：{run_id}",
        f"导出版本：{summary['export_revision'] or '未知'}",
        f"状态：{summary['status']}",
        f"阶段：{summary['last_stage'] or '未知'}",
        f"原因码：{summary['reason_code'] or '无记录'}",
        f"事务结果：{summary['transaction_outcome']}",
        f"投递状态：{summary['delivery'] or '无记录'}",
        f"诊断缺口：{', '.join(summary['diagnostic_gaps']) or '无'}",
        "", "预期：", "实际：",
    ])
    return make_draft(run_id=run_id, schema=SCHEMA, subject=subject, body=body)
