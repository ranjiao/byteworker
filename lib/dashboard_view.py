"""Read-only dashboard projection shared by the CLI and Viewer."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from dashboard_settings import DashboardError, LEGACY_PATH, load_dashboard_settings, safe_path
from frontmatter import extract_tldr, parse_file
from kb_query import evidence_rows
from provenance import list_value, source_title_from_frontmatter, source_url_from_frontmatter
from todo_store import load_todos
from todo_time import load_preferences, local_now, parse_iso


PROGRESS_LIMIT = 30
EVIDENCE_MARKER = re.compile(r"\[(E[1-9][0-9]*)\]")


def dashboard_inputs(kb: Path) -> dict:
    settings, source, baseline = load_dashboard_settings(kb.resolve())
    return {
        "schema_version": "byteworker-dashboard-inputs/v1",
        "source": source,
        "base_sha256": baseline,
        "watches": [
            {"title": item.title, "node_id": item.node_id, "note": item.note}
            for item in settings.watches
        ],
        "reminders": list(settings.reminders),
    }


def _text(value: object) -> str:
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", " ".join(str(value).split()))


def _http_url(value: str) -> str:
    if not value.startswith(("https://", "http://")) or re.search(r'[\s<>|]', value):
        return ""
    try:
        return value if urlsplit(value).netloc else ""
    except ValueError:
        return ""


def _citation(kb: Path, source: str, target: str = "", source_time: str = "") -> str:
    raw = {}
    if re.fullmatch(r"raw-[\w.-]+", source):
        path = safe_path(kb, f"raw_data/{source}.md")
        if path.is_file():
            raw, _ = parse_file(str(path))
    url = _http_url(target) or _http_url(source_url_from_frontmatter(raw)) or _http_url(source)
    title = source_title_from_frontmatter(raw) if raw else "原始来源"
    original = f"[{_text(title)}](<{url}>)" if url else f"{_text(title)}，原始链接未记录"
    source_time = source_time or next(
        (raw[key] for key in ("source_window", "digest_period", "event_time", "source_created_at") if raw.get(key)), ""
    )
    ingested = raw.get("ingested")
    details = [
        f"原文时间/覆盖 {_text(source_time)}" if source_time else "原文时间未记录",
        f"收录 {_text(ingested)}" if ingested else "收录时间未记录",
        f"版本 {_text(raw.get('source_revision') or raw.get('revision_id') or '未记录')}",
        f"置信度 {'高' if url and ingested and target and _http_url(target) else '中'}",
    ]
    if source and not _http_url(source):
        details.append(_text(source))
    return original + " · " + " · ".join(details)


def _watch_summary(kb: Path, meta: dict, body: str, citations: list[str]) -> str:
    summary = extract_tldr(body)
    if not summary:
        return _text(meta.get("status") or "节点未记录摘要")
    rows = evidence_rows(body) if EVIDENCE_MARKER.search(summary) else {}
    references = []
    for marker in dict.fromkeys(EVIDENCE_MARKER.findall(summary)):
        row = rows.get(marker)
        if row:
            references.append(_citation(kb, row["raw_id"], row["target"], row["source_time"]))
    sources = list_value(meta.get("sources"))
    if not rows and len(sources) == 1:
        references.append(_citation(kb, sources[0]))
    result = _text(EVIDENCE_MARKER.sub("", summary))
    if not references:
        return result + "（原始出处未定位，置信度中）"
    labels = []
    for item in dict.fromkeys(references):
        if item not in citations:
            citations.append(item)
        labels.append(f" [S{citations.index(item) + 1}]")
    return result + "".join(labels)


def _todo_alerts(kb: Path, now: datetime, hours: int) -> list[str]:
    _, todos = load_todos(safe_path(kb, "todo.md"))
    overdue, soon = [], []
    for todo in todos:
        if todo.status not in {"open", "waiting"}:
            continue
        due = parse_iso(todo.fields.get("due_at", ""), now.tzinfo)
        if due is None:
            continue
        if due < now:
            overdue.append((due, todo.title))
        elif due <= now + timedelta(hours=hours):
            soon.append((due, todo.title))
    result = []
    remaining = 3
    for label, items in (("逾期", overdue), ("临期", soon)):
        if items:
            titles = "、".join(_text(title) for _, title in sorted(items)[:remaining])
            result.append(f"- Todo {label} {len(items)} 项" + (f" · {titles}" if titles else ""))
            remaining = max(0, remaining - len(items))
    return result


def dashboard_view(kb: Path, *, at: str | None = None) -> dict:
    kb = kb.resolve()
    settings, source, _ = load_dashboard_settings(kb)
    safe_path(kb, "context.md")
    prefs = load_preferences(kb)
    now = local_now(at, prefs)
    today = now.date()
    nodes = {}
    watched = {item.node_id for item in settings.watches if item.node_id}
    paths = sorted(safe_path(kb, "knowledge").rglob("*.md")) if watched else ()
    for path in paths:
        if path.is_symlink():
            continue
        meta, body = parse_file(str(safe_path(kb, path.relative_to(kb).as_posix())))
        node_id = str(meta.get("id") or "")
        if node_id not in watched:
            continue
        if node_id in nodes:
            raise DashboardError("DASHBOARD_NODE_DUPLICATE", f"节点 ID 重复: {node_id}")
        nodes[node_id] = (meta, body)

    citations: list[str] = []
    lines = [
        "# 工作看板", "",
        f"> 生成于 {now:%Y-%m-%d %H:%M}（{prefs.timezone}）", "",
        "## 📌 长期关注", "",
    ]
    if settings.watches:
        lines += ["| 关注项 | 绑定节点 | 关注什么 | 当前状态 |", "| --- | --- | --- | --- |"]
    else:
        lines.append("暂无长期关注。")
    for watch in settings.watches:
        node = nodes.get(watch.node_id)
        if node:
            summary = _watch_summary(kb, *node, citations)
        else:
            summary = "未找到绑定节点" if watch.node_id else "—"
        lines.append(f"| {watch.title} | {watch.node} | {watch.note} | {summary} |")
    if source == LEGACY_PATH:
        lines += ["", "> 手动项来自旧看板，等待迁移到 settings.md。"]
    lines += ["", "## ⚠️ 需要关注", "", "### 看板提醒", ""]
    lines += list(settings.reminders) or ["暂无手动提醒。"]
    lines += ["", "### 待办", ""]
    lines += _todo_alerts(kb, now, prefs.due_soon_hours) or ["暂无逾期或临期待办。"]
    journal = safe_path(kb, f"journal/{today:%Y-%m}/{today.isoformat()}.md")
    operations = []
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            if re.match(r"^- \d{2}:\d{2}\b", line):
                operations.append(line[2:])
            elif operations and line.startswith(("  ", "\t")):
                operations[-1] += " " + line.strip()
    lines += ["", f"## 📅 今日进展（{today}）", "", "以下为知识库操作记录。", ""]
    lines += [f"- {_text(item)}" for item in operations[-PROGRESS_LIMIT:]] or ["今天暂无操作记录。"]
    if len(operations) > PROGRESS_LIMIT:
        lines.append(f"- 共 {len(operations)} 条操作，显示最近 {PROGRESS_LIMIT} 条。")
    if citations:
        lines += ["", "## 引用", ""]
        lines += [f"- [S{i}] {item}" for i, item in enumerate(citations, 1)]
    return {
        "schema_version": "byteworker-dashboard-view/v1",
        "generated_at": now.isoformat(timespec="seconds"),
        "markdown": "\n".join(lines) + "\n",
    }
