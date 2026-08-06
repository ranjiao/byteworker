"""Validate one semantic report and render host-neutral report artifacts."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from dreaming_state import (
    DreamingError,
    FILE_MODE,
    _secure_chmod,
    _secure_fchmod,
    atomic_write_json,
    secure_path,
)


REPORT_DOCUMENT_SCHEMA = "byteworker-report-document/v1"
REPORT_ARTIFACT_SCHEMA = "byteworker-report-artifacts/v1"
SECTION_ORDER = (
    ("highlights", "今日重点"),
    ("changes", "夜间重要变化"),
    ("risks", "风险 / 阻塞"),
    ("confirmations", "待确认"),
    ("todos", "你的 Todo"),
)
SEVERITIES = {"info", "attention", "high"}
MAX_INPUT_BYTES = 2 * 1024 * 1024
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
HTML_TEMPLATE = TEMPLATE_DIR / "report-template.html"
FORBIDDEN_HTML_TEMPLATE_TOKENS = (
    "<link",
    "@import",
    "http://",
    "https://",
)
FORBIDDEN_HTML_TEMPLATE_PATTERNS = (
    re.compile(r"<script\b[^>]*\bsrc\s*=", re.IGNORECASE),
    re.compile(
        r"<(?:img|iframe|audio|video|source|embed|object)\b[^>]*\bsrc\s*=",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|eval)\s*\(",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:localStorage|sessionStorage|document\.cookie)\b"),
)
SEVERITY_CLASS = {
    "high": "lv-high",
    "attention": "lv-attn",
    "info": "lv-info",
}
SEVERITY_LABEL = {
    "high": "高优",
    "attention": "关注",
    "info": "常规",
}
SECTION_META = {
    "changes": ("夜间重要变化", "Change", "lv-info"),
    "risks": ("风险 / 阻塞", "Risk", "lv-high"),
    "confirmations": ("待确认", "Confirm", "lv-attn"),
    "todos": ("你的 Todo", "Todo", "lv-info"),
}


def _text(value: object, field: str, *, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DreamingError("DREAMING_REPORT_DOCUMENT_INVALID", f"{field} 不能为空。")
    result = value.strip()
    if len(result) > maximum:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            f"{field} 超过 {maximum} 字符。",
        )
    return result


def _optional_text(value: object, field: str, *, maximum: int = 4000) -> str:
    if value in (None, ""):
        return ""
    return _text(value, field, maximum=maximum)


def _string_list(value: object, field: str, *, maximum: int = 50) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            f"{field} 必须是最多 {maximum} 项的数组。",
        )
    return [_text(item, f"{field}[]", maximum=200) for item in value]


def _validate_document(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != REPORT_DOCUMENT_SCHEMA:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            f"schema_version 必须是 {REPORT_DOCUMENT_SCHEMA}。",
        )
    kind = _text(value.get("kind"), "kind", maximum=20)
    if kind not in {"morning", "daily", "weekly"}:
        raise DreamingError("DREAMING_REPORT_DOCUMENT_INVALID", "kind 无效。")
    period = _text(value.get("period"), "period", maximum=20)
    try:
        if kind == "weekly":
            match = re.fullmatch(r"(\d{4})-W(\d{2})", period)
            if match is None:
                raise ValueError
            date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
        else:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period) is None:
                raise ValueError
            date.fromisoformat(period)
    except ValueError as exc:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "period 与报告类型不匹配。",
        ) from exc
    generated_at = _text(value.get("generated_at"), "generated_at", maximum=50)
    window = value.get("window")
    coverage = value.get("coverage")
    sections = value.get("sections")
    sources = value.get("sources")
    if not isinstance(window, Mapping) or not isinstance(coverage, Mapping):
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "window 和 coverage 必须是 object。",
        )
    if not isinstance(sections, Mapping) or not isinstance(sources, list):
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "sections 必须是 object，sources 必须是 array。",
        )
    summary = _text(value.get("message_summary"), "message_summary", maximum=500)
    if len(summary) < 300:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "message_summary 必须为 300-500 字。",
        )
    normalized_sections: dict[str, list[dict[str, Any]]] = {}
    source_ids: set[str] = set()
    normalized_sources = []
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise DreamingError(
                "DREAMING_REPORT_DOCUMENT_INVALID",
                f"sources[{index}] 必须是 object。",
            )
        source_id = _text(source.get("id"), f"sources[{index}].id", maximum=40)
        if source_id in source_ids:
            raise DreamingError(
                "DREAMING_REPORT_DOCUMENT_INVALID",
                f"重复 source id: {source_id}",
            )
        source_ids.add(source_id)
        normalized_sources.append(
            {
                "id": source_id,
                "title": _text(source.get("title"), f"sources[{index}].title"),
                "type": _text(source.get("type"), f"sources[{index}].type", maximum=80),
                "locator": _text(
                    source.get("locator"),
                    f"sources[{index}].locator",
                    maximum=4000,
                ),
                "observed_at": _optional_text(
                    source.get("observed_at"),
                    f"sources[{index}].observed_at",
                    maximum=100,
                ),
                "confidence": _optional_text(
                    source.get("confidence"),
                    f"sources[{index}].confidence",
                    maximum=100,
                ),
            }
        )
    for key, _ in SECTION_ORDER:
        items = sections.get(key, [])
        if not isinstance(items, list) or len(items) > 50:
            raise DreamingError(
                "DREAMING_REPORT_DOCUMENT_INVALID",
                f"sections.{key} 必须是最多 50 项的数组。",
            )
        normalized = []
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise DreamingError(
                    "DREAMING_REPORT_DOCUMENT_INVALID",
                    f"sections.{key}[{index}] 必须是 object。",
                )
            severity = str(item.get("severity", "info"))
            if severity not in SEVERITIES:
                raise DreamingError(
                    "DREAMING_REPORT_DOCUMENT_INVALID",
                    f"sections.{key}[{index}].severity 无效。",
                )
            refs = _string_list(
                item.get("source_refs", []),
                f"sections.{key}[{index}].source_refs",
            )
            unknown = sorted(set(refs) - source_ids)
            if unknown:
                raise DreamingError(
                    "DREAMING_REPORT_DOCUMENT_INVALID",
                    f"sections.{key}[{index}] 引用了未知来源。",
                    details={"source_refs": unknown},
                )
            normalized.append(
                {
                    "title": _text(
                        item.get("title"),
                        f"sections.{key}[{index}].title",
                        maximum=300,
                    ),
                    "detail": _optional_text(
                        item.get("detail"),
                        f"sections.{key}[{index}].detail",
                    ),
                    "severity": severity,
                    "source_refs": refs,
                }
            )
        normalized_sections[key] = normalized
    coverage_status = _text(coverage.get("status"), "coverage.status", maximum=20)
    if coverage_status not in {"covered", "partial"}:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "coverage.status 必须是 covered 或 partial。",
        )
    return {
        "schema_version": REPORT_DOCUMENT_SCHEMA,
        "kind": kind,
        "period": period,
        "title": _text(value.get("title"), "title", maximum=200),
        "generated_at": generated_at,
        "window": {
            "start": _text(window.get("start"), "window.start", maximum=100),
            "end": _text(window.get("end"), "window.end", maximum=100),
            "timezone": _text(window.get("timezone"), "window.timezone", maximum=100),
        },
        "coverage": {
            "status": coverage_status,
            "notes": _string_list(coverage.get("notes", []), "coverage.notes"),
        },
        "message_summary": summary,
        "sections": normalized_sections,
        "sources": normalized_sources,
        "manual_notes": _optional_text(value.get("manual_notes"), "manual_notes"),
    }


def load_report_document(path: Path, *, skill_root: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved == skill_root.resolve() or skill_root.resolve() in resolved.parents:
        raise DreamingError(
            "DREAMING_OUTPUT_IN_SKILL_REPO",
            "报告输入含业务内容，不得位于 byteworker skill 仓库。",
        )
    try:
        if resolved.stat().st_size > MAX_INPUT_BYTES:
            raise DreamingError(
                "DREAMING_REPORT_DOCUMENT_INVALID",
                "报告输入超过 2 MiB。",
            )
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except DreamingError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "无法读取结构化报告 JSON。",
        ) from exc
    if not isinstance(value, Mapping):
        raise DreamingError(
            "DREAMING_REPORT_DOCUMENT_INVALID",
            "结构化报告顶层必须是 object。",
        )
    return _validate_document(value)


def _refs(values: Sequence[str]) -> str:
    return "".join(f"[{item}]" for item in values)


def _render_markdown(document: Mapping[str, Any]) -> str:
    window = document["window"]
    coverage = document["coverage"]
    lines = [
        f"# {document['title']}",
        "",
        f"> 生成时间:{document['generated_at']}",
        f"> 范围:{window['start']} .. {window['end']}",
        f"> Coverage:{coverage['status']}",
    ]
    for note in coverage["notes"]:
        lines.append(f"> Coverage 说明:{note}")
    for key, heading in SECTION_ORDER:
        lines.extend(["", f"## {heading}"])
        items = document["sections"][key]
        if not items:
            lines.append("- 暂无")
            continue
        for item in items:
            detail = f"：{item['detail']}" if item["detail"] else ""
            lines.append(f"- {item['title']}{detail}{_refs(item['source_refs'])}")
    lines.extend(["", "## 引用"])
    if not document["sources"]:
        lines.append("- 暂无")
    for source in document["sources"]:
        suffix = []
        if source["observed_at"]:
            suffix.append(f"时间:{source['observed_at']}")
        if source["confidence"]:
            suffix.append(f"置信度:{source['confidence']}")
        details = "；".join(suffix)
        lines.append(
            f"- [{source['id']}] 《{source['title']}》— {source['type']}；"
            f"原始出处:{source['locator']}"
            + (f"；{details}" if details else "")
        )
    lines.extend(
        [
            "",
            "## 手动补充 / 备注",
            document["manual_notes"] or "- 暂无",
            "",
        ]
    )
    return "\n".join(lines)


def _existing_manual_notes(path: Path) -> str:
    if path.is_symlink():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    marker = "\n## 手动补充 / 备注\n"
    if marker not in content:
        return ""
    notes = content.split(marker, 1)[1].strip()
    return "" if notes == "- 暂无" else notes


def _render_html(document: Mapping[str, Any]) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    coverage = document["coverage"]
    window = document["window"]
    section_cards = _render_html_section_cards(document)
    highlights = _render_html_highlights(document)
    sources = _render_html_sources(document)
    notes = "".join(
        f'<span class="note">{esc(note)}</span>' for note in coverage["notes"]
    ) or '<span class="note">暂无</span>'
    source_counts = _source_counts(document)
    total_items = sum(len(document["sections"][key]) for key, _ in SECTION_ORDER)
    high_items = sum(
        1
        for key, _ in SECTION_ORDER
        for item in document["sections"][key]
        if item["severity"] == "high"
    )
    confidence = "高" if coverage["status"] == "covered" else "中"
    replacements = {
        "TITLE": esc(document["title"]),
        "REPORT_TITLE": esc(document["title"]),
        "GENERATED_AT": esc(document["generated_at"]),
        "WINDOW_START": esc(window["start"]),
        "WINDOW_END": esc(window["end"]),
        "TIMEZONE": esc(window["timezone"]),
        "COVERAGE_STATUS": esc(coverage["status"]),
        "CONFIDENCE": confidence,
        "COVERAGE_NOTES": notes,
        "SECTION_CARDS": section_cards,
        "SECTIONS_HTML": section_cards,
        "STATS": _render_html_stats(total_items, len(document["sources"]), high_items, confidence),
        "SOURCE_TABS": _render_html_source_tabs(document["sources"], source_counts),
        "HIGHLIGHTS": highlights,
        "CHANGES": _render_html_rows("changes", document["sections"]["changes"]),
        "RISKS": _render_html_rows("risks", document["sections"]["risks"]),
        "CONFIRMATIONS": _render_html_rows(
            "confirmations",
            document["sections"]["confirmations"],
        ),
        "TODOS": _render_html_rows("todos", document["sections"]["todos"]),
        "SOURCES": sources,
        "SOURCES_HTML": sources,
        "MANUAL_NOTES": esc(document["manual_notes"] or "暂无"),
    }
    replacements.update({key.lower(): value for key, value in replacements.items()})
    template = _load_html_template()
    for key, value in replacements.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _source_counts(document: Mapping[str, Any]) -> dict[str, int]:
    counts = {source["id"]: 0 for source in document["sources"]}
    for key, _ in SECTION_ORDER:
        for item in document["sections"][key]:
            for source_id in item["source_refs"]:
                counts[source_id] = counts.get(source_id, 0) + 1
    return counts


def _source_ref_label(source_id: str) -> str:
    match = re.fullmatch(r"S(\d+)", source_id)
    return f"来源{match.group(1)}" if match else source_id


def _source_ref_links(refs: Sequence[str], *, compact: bool = False) -> str:
    if not refs:
        return ""
    links = []
    for source_id in refs:
        escaped_id = html.escape(source_id, quote=True)
        label = html.escape(_source_ref_label(source_id), quote=True)
        text = f"[{label}]" if not compact else f"[{escaped_id}]"
        links.append(f'<a class="ref" href="#src-{escaped_id}">{text}</a>')
    return "".join(links)


def _item_data_src(refs: Sequence[str]) -> str:
    return html.escape(" ".join(refs), quote=True)


def _severity_class(severity: str) -> str:
    return SEVERITY_CLASS.get(severity, "lv-info")


def _severity_label(severity: str) -> str:
    return SEVERITY_LABEL.get(severity, "常规")


def _render_html_stats(
    total_items: int,
    source_count: int,
    high_items: int,
    confidence: str,
) -> str:
    return "\n".join(
        (
            f'<div class="stat"><span class="stat-k">ITEMS</span><span class="stat-v">{total_items}</span></div>',
            f'<div class="stat"><span class="stat-k">SOURCES</span><span class="stat-v">{source_count}</span></div>',
            f'<div class="stat"><span class="stat-k">HIGH</span><span class="stat-v accent">{high_items}</span></div>',
            f'<div class="stat"><span class="stat-k">CONF</span><span class="stat-v">{html.escape(confidence, quote=True)}</span></div>',
        )
    )


def _render_html_source_tabs(
    sources: Sequence[Mapping[str, Any]],
    counts: Mapping[str, int],
) -> str:
    total = sum(counts.values())
    tabs = [
        '<button type="button" class="tab is-on" data-tab="all">'
        f'全部来源<span class="pill" data-count="all">{total}</span></button>'
    ]
    for source in sources:
        source_id = html.escape(source["id"], quote=True)
        title = html.escape(source["title"], quote=True)
        count = counts.get(source["id"], 0)
        tabs.append(
            f'<button type="button" class="tab" data-tab="{source_id}">'
            f'{title}<span class="pill" data-count="{source_id}">{count}</span></button>'
        )
    return "\n".join(tabs)


def _render_html_highlights(document: Mapping[str, Any]) -> str:
    rendered = []
    for index, item in enumerate(document["sections"]["highlights"], start=1):
        severity = item["severity"]
        level_class = _severity_class(severity)
        level_label = _severity_label(severity)
        refs = _source_ref_links(item["source_refs"])
        detail = html.escape(item["detail"] or "详见引用来源。", quote=True)
        rendered.append(
            f'<button type="button" class="card item {level_class}" '
            f'data-item data-src="{_item_data_src(item["source_refs"])}">\n'
            f'  <span class="row-top"><span class="row-left">'
            f'<span class="num">{index:02d}</span><span class="lv">{level_label}</span>'
            f'</span><span class="hint">展开</span></span>\n'
            f'  <span class="card-title">{html.escape(item["title"], quote=True)}</span>\n'
            f'  <span class="lede">{detail}</span>\n'
            f'  <span class="detail">{detail}</span>\n'
            f'  <span class="refs">{refs}</span>\n'
            f'</button>'
        )
    return "\n".join(rendered) or '<p class="empty">暂无</p>'


def _render_html_section_cards(document: Mapping[str, Any]) -> str:
    sections = []
    for key in ("changes", "risks", "confirmations", "todos"):
        items = document["sections"][key]
        heading, code, default_level = SECTION_META[key]
        rows = _render_html_rows(key, items)
        sections.append(
            f'<section class="block" data-block data-reveal>\n'
            f'  <div class="block-head"><span class="bar {default_level}"></span>'
            f'<h2>{html.escape(heading, quote=True)}</h2><span class="code">{code}</span>'
            f'<span class="spacer"></span><span class="mono muted">'
            f'<span data-block-count>{len(items)}</span> 条</span></div>\n'
            f'  <div class="thead"><span>对象</span><span>结论 / 说明</span>'
            f'<span>等级</span><span>来源</span></div>\n'
            f'{rows}\n'
            f'</section>'
        )
    return "\n\n".join(sections)


def _render_html_rows(key: str, items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return '<div class="row"><span class="empty">暂无</span></div>'
    heading, _, _ = SECTION_META[key]
    rows = []
    for item in items:
        level_class = _severity_class(item["severity"])
        level_label = _severity_label(item["severity"])
        detail = html.escape(item["detail"] or "暂无补充说明。", quote=True)
        refs = _source_ref_links(item["source_refs"], compact=True)
        source_cell = refs + '<span class="hint">展开</span>'
        rows.append(
            f'<button type="button" class="item row {level_class}" '
            f'data-item data-src="{_item_data_src(item["source_refs"])}">\n'
            f'  <span class="subj">{html.escape(heading, quote=True)}<span class="tick"></span></span>\n'
            f'  <span class="cell"><span class="row-title">{html.escape(item["title"], quote=True)}</span>'
            f'<span class="lede">{detail}</span><span class="detail">{detail}</span></span>\n'
            f'  <span class="lv">{level_label}</span>\n'
            f'  <span class="cell">{source_cell}</span>\n'
            f'</button>'
        )
    return "\n".join(rows)


def _render_html_sources(document: Mapping[str, Any]) -> str:
    sources = []
    for source in document["sources"]:
        source_id = html.escape(source["id"], quote=True)
        source_type = html.escape(source["type"], quote=True)
        confidence = html.escape(source["confidence"] or "中", quote=True)
        confidence_class = "conf-high" if source["confidence"] == "高" else "conf-mid"
        meta = f'{source_type} · 原始出处:{source["locator"]}'
        if source["observed_at"]:
            meta += f' · 时间:{source["observed_at"]}'
        sources.append(
            f'<div class="src" id="src-{source_id}">\n'
            f'  <span class="cell"><span class="mono ref-static">[{source_id}]</span>'
            f'<span class="mono kind">{source_type}</span></span>\n'
            f'  <span class="cell"><span class="src-name">{html.escape(source["title"], quote=True)}</span>'
            f'<span class="src-meta">{html.escape(meta, quote=True)}</span></span>\n'
            f'  <span class="mono conf {confidence_class}">可信度 {confidence}</span>\n'
            f'</div>'
        )
    return "\n".join(sources) or '<div class="src"><span class="empty">暂无</span></div>'


def _load_html_template() -> str:
    path = HTML_TEMPLATE
    if path.is_file():
        template = path.read_text(encoding="utf-8")
        lowered = template.lower()
        forbidden = [
            token
            for token in FORBIDDEN_HTML_TEMPLATE_TOKENS
            if token in lowered
        ]
        forbidden.extend(
            pattern.pattern
            for pattern in FORBIDDEN_HTML_TEMPLATE_PATTERNS
            if pattern.search(template)
        )
        if forbidden:
            raise DreamingError(
                "DREAMING_REPORT_TEMPLATE_UNSAFE",
                "HTML 模板必须自包含，不能包含外部资源或脚本入口。",
                details={"path": str(path), "forbidden": forbidden},
            )
        return template
    raise DreamingError(
        "DREAMING_REPORT_TEMPLATE_MISSING",
        "未找到 Dreaming HTML 报告模板。",
        details={"path": str(path)},
    )


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _secure_chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _secure_fchmod(handle.fileno(), FILE_MODE)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _secure_chmod(path, FILE_MODE)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_archive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".report-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o644)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o644)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def render_report_bundle(
    kb: Path,
    *,
    document: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _validate_document(document)
    archive_path = (
        kb.resolve()
        / "reports"
        / normalized["kind"]
        / f"{normalized['period']}.md"
    )
    if not normalized["manual_notes"]:
        preserved_notes = _existing_manual_notes(archive_path)
        if preserved_notes:
            normalized["manual_notes"] = preserved_notes
    directory = secure_path(
        kb,
        "reports",
        f"{normalized['kind']}-{normalized['period']}",
        "artifacts",
    )
    document_path = directory / "report.json"
    summary_path = directory / "summary.txt"
    markdown_path = directory / "report.md"
    html_path = directory / "report.html"
    atomic_write_json(document_path, normalized)
    _write_private(summary_path, normalized["message_summary"] + "\n")
    markdown = _render_markdown(normalized)
    _write_private(markdown_path, markdown)
    _write_private(html_path, _render_html(normalized))
    _write_archive(archive_path, markdown)
    artifacts = {}
    for name, path, media_type, audience in (
        ("document", document_path, "application/json", "internal"),
        ("summary", summary_path, "text/plain", "user"),
        ("markdown", markdown_path, "text/markdown", "internal"),
        ("html", html_path, "text/html", "user"),
    ):
        artifacts[name] = {
            "path": str(path.relative_to(kb.resolve())),
            "absolute_path": str(path),
            "media_type": media_type,
            "audience": audience,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    manifest = {
        "schema_version": REPORT_ARTIFACT_SCHEMA,
        "kind": normalized["kind"],
        "period": normalized["period"],
        "archive_path": str(archive_path.relative_to(kb.resolve())),
        "artifacts": artifacts,
        "host_delivery": {
            "summary": "return_inline",
            "html": "preview_or_file_link",
            "host_specific_api_required": False,
        },
    }
    manifest_path = directory / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    return {
        "status": "rendered",
        "kind": normalized["kind"],
        "period": normalized["period"],
        "manifest_path": str(manifest_path.relative_to(kb.resolve())),
        "report_path": str(archive_path.relative_to(kb.resolve())),
        "summary": normalized["message_summary"],
        "html_path": str(html_path),
        "markdown_path": str(markdown_path),
    }
