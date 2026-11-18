"""Reader-facing projection of a private Dreaming result and its recorded outcomes."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
import json
from pathlib import Path
import re

from dreaming_state import DreamingError, parse_time, secure_path

SOURCE_OUTCOMES = {
    "committed": "已收录", "noop": "与上次相同", "empty_window": "没有新消息",
    "observed": "已检查", "covered": "此前已完成", "failed": "失败",
    "blocked": "暂缓", "pending": "尚未完成",
}
FAILURE_REASONS = {
    "SOURCE_AUTH_REQUIRED": "需要重新登录来源",
    "SOURCE_PERMISSION_DENIED": "没有读取权限",
    "SOURCE_RATE_LIMITED": "来源暂时限制读取次数",
    "DIGEST_BUDGET_DEFERRED": "内容超过本次可处理的范围",
    "DIGEST_HOST_CONTEXT_LIMIT": "内容过长，本次未读完",
}


def _source_lines(sources, notes):
    lines = []
    for documents in (True, False):
        rows = [row for row in sources if (row.get("source_type") in {"feishu_doc", "local_md"}) == documents]
        if not rows:
            continue
        counts = Counter(SOURCE_OUTCOMES.get(row.get("status"), "结果未知") for row in rows)
        completed = sum(row.get("status") in {"committed", "noop", "empty_window", "observed"} for row in rows)
        unit = "份" if documents else "个"
        group = f"{'文档' if documents else '其他来源'}共 {len(rows)} {unit}，本次完成 {completed} {unit}"
        lines.append(group + "，" + "、".join(f"{label} {count}" for label, count in counts.items()) + "。")
        for index, row in enumerate(rows, 1):
            label = row.get("label") or f"未命名{'文档' if documents else '来源'} {index}"
            status = SOURCE_OUTCOMES.get(row.get("status"), "结果未知")
            note = notes.get(row.get("source_key")) or FAILURE_REASONS.get(row.get("error_code"))
            lines.append(f"· {label}　{status}" + (f"。{note}" if note else ""))
    return lines


def _recovery_messages(kb, document, accounting):
    """Resolve a single explicitly cited, committed batch inside this run's lifetime."""
    batch = accounting.get("message_batch")
    if not batch:
        checks = " ".join(str(row.get("detail", "")) for row in document.get("checks", []) if isinstance(row, dict))
        cited = set(re.findall(r"(?<![A-Za-z0-9])EB-[0-9a-f]{32}(?![0-9a-f])", checks))
        if len(cited) != 1:
            return []
        batch = cited.pop()
    if not isinstance(batch, str) or not re.fullmatch(r"EB-[0-9a-f]{32}", batch):
        return []
    end = parse_time(document.get("recorded_at"))
    duration = accounting.get("metrics", {}).get("duration_ms")
    if end is None or not isinstance(duration, int):
        return []
    try:
        manifest = json.loads(secure_path(kb, "batches", batch, "manifest.json").read_text())
        commit = json.loads(secure_path(kb, "batches", batch, "batch.commit.json").read_text())
        created, committed = parse_time(manifest.get("created_at")), parse_time(commit.get("committed_at"))
        if (manifest.get("batch_id") != batch or commit.get("batch_id") != batch
                or manifest.get("source", {}).get("lane") not in {"monitored", "discovery"}
                or commit.get("stage") != "committed" or not created or not committed
                or not end - timedelta(milliseconds=duration + 1) <= created <= committed <= end):
            return []
        lines = [f"读了 {len(manifest['items'])} 条历史消息。"]
        history = secure_path(kb, "finding-history.jsonl")
        if history.is_file():
            with history.open() as events:
                for line in events:
                    event = json.loads(line)
                    if event.get("batch_id") == batch and event.get("operation") == "upsert":
                        summary = event.get("proposal", {}).get("summary")
                        if summary:
                            lines.append(summary)
                            for caveat in event["proposal"].get("uncertainties", []):
                                if isinstance(caveat, str) and caveat and caveat not in lines:
                                    lines.append(caveat)
        if len(lines) == 1 and not accounting.get("message_batch"):
            return []
        gaps = manifest.get("coverage", {}).get("gaps", [])
        if gaps:
            lines.append(f"还有 {len(gaps)} 段消息未读全。")
        return lines
    except (DreamingError, OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return []


def run_brief(kb: Path, document: dict) -> str:
    accounting = document.get("accounting") or {}
    lines = []
    if document.get("job") == "recovery":
        lines = _recovery_messages(kb, document, accounting)
    # New summaries are authored for readers. For older message recovery runs,
    # prefer the contemporaneous finding over the runner's technical narrative.
    if not lines or accounting.get("message_batch"):
        summary = document.get("summary", "")
        if summary and summary != "本轮未记录主要信息摘要。" and summary not in lines:
            lines.insert(0, summary)
    if document.get("job") == "maintenance":
        for repair in document.get("repairs", []):
            lines.append(f"已修复 {repair['path']}。{repair.get('detail') or repair['action']}")
    sources = accounting.get("routine_sources", [])
    lines.extend(_source_lines(sources, document.get("source_notes", {})))
    nodes = accounting.get("changed_nodes")
    if nodes:
        for action in ("新增", "更新", "删除"):
            names = [node["title"] for node in nodes if node["action"] == action]
            if names:
                lines.append(f"{action} {len(names)} 个知识节点：" + "、".join(f"《{name}》" for name in names) + "。")
    elif sources:
        totals = accounting.get("kb_operations") or {}
        counts = [f"{label} {totals[key]} 个" for key, label in (
            ("nodes_created", "新增"), ("nodes_updated", "更新"), ("nodes_deleted", "删除"),
        ) if totals.get(key)]
        if counts:
            lines.append("知识节点" + "、".join(counts) + "。这次记录未保存节点名称。")
        elif nodes == [] and not accounting.get("unaccounted_sources") and not accounting.get("unknown_action_changes"):
            lines.append("知识节点没有变化。")
    if accounting.get("unaccounted_sources") or accounting.get("unknown_action_changes"):
        lines.append("部分来源或操作缺少可核对的节点明细。")
    return "\n".join(lines) or "这次运行没有留下可核对的内容摘要。"
