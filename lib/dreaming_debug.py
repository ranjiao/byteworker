"""Read-only Dreaming run audit projection for the local debug viewer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_run_log import show_run
from dreaming_state import DreamingError, parse_time, secure_path


MAX_EVIDENCE_CHARS = 1600


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _run_window(events: list[Mapping[str, Any]]) -> tuple[datetime | None, datetime]:
    started = parse_time(events[0].get("timestamp")) if events else None
    ended = parse_time(events[-1].get("timestamp")) if events else None
    return started, ended or datetime.now(timezone.utc)


def _candidate_batches(
    kb: Path,
    events: list[Mapping[str, Any]],
) -> tuple[list[str], str]:
    explicit = sorted(
        {
            str(event.get("batch_id", ""))
            for event in events
            if str(event.get("batch_id", "")).strip()
        }
    )
    if explicit:
        return explicit, "explicit"
    started, ended = _run_window(events)
    if started is None:
        return [], "none"
    root = secure_path(kb, "batches")
    if not root.is_dir():
        return [], "none"
    inferred = []
    for manifest_path in root.glob("EB-*/manifest.json"):
        manifest = _read_json(manifest_path)
        created = parse_time(manifest.get("created_at")) if manifest else None
        if created is not None and started <= created <= ended:
            inferred.append(str(manifest.get("batch_id", "")))
    return sorted(item for item in inferred if item), "time_window" if inferred else "none"


def _evidence_projection(
    kb: Path,
    batch_id: str,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    content_ref = str(item.get("content_ref", ""))
    prefix = f"spool://{batch_id}/"
    content: dict[str, Any] | None = None
    if content_ref.startswith(prefix):
        name = content_ref[len(prefix) :]
        if "/" not in name and name.endswith(".json"):
            content = _read_json(secure_path(kb, "spool", batch_id, name))
    raw = str((content or {}).get("content", ""))
    return {
        "item_id": str(item.get("item_id", "")),
        "occurred_at": item.get("occurred_at"),
        "chat_name": (content or {}).get("chat_name", ""),
        "chat_type": (content or {}).get("chat_type", ""),
        "sender_name": ((content or {}).get("sender") or {}).get("name", "")
        if isinstance((content or {}).get("sender"), Mapping)
        else "",
        "message_id": (content or {}).get("message_id", ""),
        "content": raw[:MAX_EVIDENCE_CHARS],
        "content_truncated": len(raw) > MAX_EVIDENCE_CHARS,
        "available": content is not None,
    }


def _batch_projection(kb: Path, batch_id: str) -> dict[str, Any]:
    root = secure_path(kb, "batches", batch_id)
    manifest = _read_json(root / "manifest.json") or {}
    analysis = _read_json(root / "analysis.receipt.json")
    consolidation = _read_json(root / "consolidation.receipt.json")
    commit = _read_json(root / "batch.commit.json")
    bundle = _read_json(root / "finding-bundle.json")
    projection = _read_json(secure_path(kb, "findings.json")) or {}
    items = manifest.get("items") if isinstance(manifest.get("items"), list) else []
    item_index = {
        str(item.get("item_id", "")): item
        for item in items
        if isinstance(item, Mapping) and item.get("item_id")
    }
    proposed_findings = (bundle or {}).get("findings", [])
    if not proposed_findings:
        proposed_findings = [
            finding
            for finding in projection.get("findings", {}).values()
            if isinstance(finding, Mapping)
            and batch_id in finding.get("batch_ids", [])
        ]
    findings = []
    unresolved_refs = []
    for finding in proposed_findings:
        if not isinstance(finding, Mapping):
            continue
        evidence = []
        for ref in finding.get("evidence_refs", []):
            item = item_index.get(str(ref))
            if item is None:
                unresolved_refs.append(str(ref))
                continue
            evidence.append(_evidence_projection(kb, batch_id, item))
        findings.append(
            {
                "finding_id": finding.get("finding_id"),
                "kind": finding.get("kind"),
                "summary": finding.get("summary"),
                "why_it_matters": finding.get("why_it_matters"),
                "confidence": finding.get("confidence"),
                "uncertainties": finding.get("uncertainties", []),
                "evidence_refs": finding.get("evidence_refs", []),
                "evidence": evidence,
            }
        )
    receipt_count = (
        int(analysis.get("finding_count", 0)) if isinstance(analysis, Mapping) else None
    )
    checks = [
        {
            "name": "batch_committed",
            "status": "pass" if commit else "pending",
            "detail": "已提交 cursor" if commit else "尚未生成 batch commit",
        },
        {
            "name": "analysis_receipt",
            "status": "pass" if analysis else "pending",
            "detail": "分析回执存在" if analysis else "尚未生成分析回执",
        },
        {
            "name": "finding_count_match",
            "status": (
                "pass"
                if receipt_count is not None and receipt_count == len(findings)
                else "pending" if receipt_count is None else "fail"
            ),
            "detail": f"回执 {receipt_count if receipt_count is not None else '—'} / 产物 {len(findings)}",
        },
        {
            "name": "evidence_resolved",
            "status": "pass" if not unresolved_refs else "fail",
            "detail": (
                "所有 Finding evidence 均可解析"
                if not unresolved_refs
                else f"{len(unresolved_refs)} 条 evidence 无法解析"
            ),
        },
    ]
    return {
        "batch_id": batch_id,
        "created_at": manifest.get("created_at"),
        "source": manifest.get("source", {}),
        "window": manifest.get("window", {}),
        "coverage": manifest.get("coverage", {}),
        "item_count": len(items),
        "analysis": analysis,
        "consolidation": consolidation,
        "commit": commit,
        "finding_count": len(findings),
        "findings": findings,
        "checks": checks,
    }


def _report_projection(kb: Path, job: str, period: str) -> dict[str, Any]:
    root = secure_path(kb, "reports", f"{job}-{period}", "artifacts")
    manifest = _read_json(root / "manifest.json")
    document = _read_json(root / "report.json")
    try:
        summary = (root / "summary.txt").read_text(encoding="utf-8").strip()
    except OSError:
        summary = ""
    return {
        "kind": "report",
        "available": bool(manifest or document or summary),
        "manifest": manifest,
        "document": document,
        "summary": summary,
        "html_url": (
            f"/kb/state/dreaming/reports/{job}-{period}/artifacts/report.html"
            if (root / "report.html").is_file()
            else ""
        ),
    }


def inspect_run(kb: Path, *, run_id: str) -> dict[str, Any]:
    run = show_run(kb, run_id=run_id)
    events = run["events"]
    first = events[0]
    job = str(first.get("job", ""))
    if job == "process":
        batch_ids, linkage = _candidate_batches(kb, events)
        result = {
            "kind": "process",
            "available": bool(batch_ids),
            "linkage": linkage,
            "batches": [_batch_projection(kb, batch_id) for batch_id in batch_ids],
        }
    elif job in {"morning", "daily", "weekly"}:
        result = _report_projection(kb, job, str(first.get("period", "")))
    else:
        document = _read_json(secure_path(kb, "run-results", f"{run_id}.json"))
        result = (
            {
                "kind": "diagnostic",
                "job": job,
                "available": True,
                "summary": document.get("summary", ""),
                "checks": document.get("checks", []),
                "repairs": document.get("repairs", []),
            }
            if document
            else {
                "kind": job,
                "available": False,
                "limitation": (
                    "该历史任务只记录了运行事件和有限计数，没有可供人工复核的结构化结果文档。"
                ),
            }
        )
    return {**run, "result": result}
