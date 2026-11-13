"""Select a rolling document window without reserializing its source text."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from frontmatter import parse_file
from source_profiles import SourceProfileError, load_profile
from ..models import SourceBundleError


_HEADING = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>|^[ \t]*#[ \t]+([^\n]+)", re.I | re.S | re.M)
_DATE = re.compile(
    r"^(?:(\d{4})[-./年]?(\d{2}|\d{1,2}(?=[-./月]))[-./月]?(\d{1,2})"
    r"|(\d{1,2})[-./月](\d{1,2})|(\d{2})(\d{2}))(?!\d)"
)
_WEEK = re.compile(r"^(\d{4})-W(\d{2})(?!\d)", re.I)
_TEMPLATE = re.compile(r"^(?:模板|模版|template)", re.I)
_BOARD_TOKEN = re.compile(r"""<whiteboard\b[^>]*?\btoken=["']([^"']+)["']""", re.I)
_BLOCK_ID = re.compile(r"""<[^<>]+?\sid=["']([^"']+)["']""")
_FRAGMENT = re.compile(r"\s*<fragment\b[^>]*>(.*)</fragment>\s*", re.S)


def _period(title: str, reference: date) -> tuple[str, date] | None:
    week = _WEEK.match(title)
    if week:
        try:
            return week[0].upper(), date.fromisocalendar(int(week[1]), int(week[2]), 1)
        except ValueError:
            return None
    match = _DATE.match(title)
    if not match:
        return None
    year, month, day, short_month, short_day, compact_month, compact_day = match.groups()
    try:
        parsed = date(int(year) if year else reference.year,
                      int(month or short_month or compact_month), int(day or short_day or compact_day))
        if year is None and parsed > reference + timedelta(days=7):
            parsed = parsed.replace(year=parsed.year - 1)
    except ValueError:
        return None
    return parsed.isoformat(), parsed


def _sections(body: str, reference: date) -> list[tuple[re.Match[str], str]]:
    sections = []
    for heading in _HEADING.finditer(body):
        title = html.unescape(re.sub(r"<[^>]*>", "", heading[1] or heading[2] or "")).strip()
        if _TEMPLATE.match(title):
            sections.append((heading, ""))
            continue
        parsed = _period(title, reference)
        if parsed:
            period, reference = parsed
            sections.append((heading, period))
    return sections


def select_document_window(body: str, *, policy: str, reference: date) -> tuple[str, str] | None:
    """Use dated H1s as boundaries. Undated subheadings stay in their period."""
    fragment = _FRAGMENT.fullmatch(body)
    if fragment:
        body = fragment[1]
        if re.search(r"<excerpt\b", body):
            raise SourceBundleError(
                "SOURCE_DOCUMENT_WINDOW_INCOMPLETE",
                "文档范围抓取只返回了容器节选，请完整抓取后重新裁切。",
                path="body",
            )
    sections = _sections(body, reference)
    for index, (heading, period) in enumerate(sections):
        if period and policy in ("latest", period):
            end = sections[index + 1][0].start() if index + 1 < len(sections) else len(body)
            # Skip empty dated placeholders.
            if body[heading.end():end].strip():
                return period, body[heading.start():end].strip()
    return None


def whiteboard_tokens(body: str) -> set[str]:
    return set(_BOARD_TOKEN.findall(body))


def routine_document_policy(kb: Path, source_uid: str) -> str:
    try:
        policy = load_profile(kb, source_uid)["capture_policy"].get("period", "")
    except SourceProfileError as exc:
        if exc.code != "SOURCE_PROFILE_NOT_FOUND":
            raise
        policy = ""
    if not policy:
        latest = None
        for path in (kb / "raw_data").glob("*.md"):
            fields, _ = parse_file(str(path))
            if (fields.get("source_type"), fields.get("source_uid"), fields.get("digest_status")) != (
                "feishu_doc", source_uid, "digested",
            ):
                continue
            try:
                ingested = datetime.fromisoformat(str(fields.get("ingested", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if ingested.tzinfo and (latest is None or ingested > latest[0]):
                latest = ingested, str(fields.get("digest_period", ""))
        if latest and latest[1]:
            policy = "latest"
    return policy


def document_fetch_scope(outline: str, *, policy: str, reference: date) -> dict[str, str]:
    if not policy:
        return {"scope": "full"}
    sections = _sections(outline, reference)
    for index, (heading, period) in enumerate(sections):
        if not period or policy not in ("latest", period):
            continue
        start = _BLOCK_ID.search(heading[0])
        end = _BLOCK_ID.search(sections[index + 1][0][0]) if index + 1 < len(sections) else None
        if start and (end or index + 1 == len(sections)):
            return {
                "scope": "range", "digest_period": period,
                "start_block_id": start[1], "end_block_id": end[1] if end else "-1",
            }
        break
    return {"scope": "full"}


def routine_document_window(
    kb: Path, source_uid: str, body: str, *, declared_period: str, today: date,
) -> tuple[str, str]:
    policy = declared_period or routine_document_policy(kb, source_uid)
    if not policy:
        return "", body
    window = select_document_window(body, policy=policy, reference=today)
    if window is None:
        raise SourceBundleError(
            "SOURCE_DOCUMENT_WINDOW_NOT_FOUND",
            "无法定位定期文档的目标日期或周次标题。",
            path="body",
        )
    return window


def scoped_document_anchors(
    anchors: list[dict[str, Any]], body: str, component_names: set[str],
) -> list[dict[str, Any]]:
    blocks = set(_BLOCK_ID.findall(body))
    return [
        anchor for anchor in anchors
        if (not anchor.get("component") or anchor["component"] in component_names)
        and (anchor["kind"] != "doc_block" or anchor["locator"].get("block_id") in blocks)
    ]
