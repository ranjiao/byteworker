"""Dreaming report windows, coverage dependencies, packets, and outbox."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dreaming_consolidation import load_findings
from dreaming_state import (
    DreamingError,
    atomic_write_json,
    load_state_unlocked,
    parse_time,
    save_state_unlocked,
    secure_path,
    state_lock,
    utc_iso,
)
from source_profiles import list_profiles
from source_profile_contract import SourceProfileError


REPORT_KINDS = {"morning", "daily", "weekly"}
WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Shanghai")
    except ZoneInfoNotFoundError as exc:
        raise DreamingError("DREAMING_REPORT_INVALID", f"无效 timezone: {name}") from exc


def report_window(
    kind: str,
    period: str,
    timezone_name: str,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if kind not in REPORT_KINDS:
        raise DreamingError("DREAMING_REPORT_INVALID", f"未知报告类型: {kind}")
    zone = _zone(timezone_name)
    try:
        if kind in {"morning", "daily"}:
            day = date.fromisoformat(period)
            if kind == "daily":
                start_local = datetime.combine(day, time.min, zone)
                end_local = start_local + timedelta(days=1)
                if as_of is not None:
                    local_as_of = as_of.astimezone(zone)
                    if local_as_of.date() == day:
                        end_local = min(end_local, local_as_of)
            else:
                end_local = datetime.combine(day, time(8, 30), zone)
                start_local = datetime.combine(day - timedelta(days=1), time(20, 30), zone)
        else:
            matched = WEEK_RE.fullmatch(period)
            if not matched:
                raise ValueError(period)
            monday = date.fromisocalendar(int(matched.group(1)), int(matched.group(2)), 1)
            start_local = datetime.combine(monday, time.min, zone)
            end_local = start_local + timedelta(days=7)
    except ValueError as exc:
        raise DreamingError(
            "DREAMING_REPORT_INVALID",
            "报告 period 必须是 YYYY-MM-DD 或 YYYY-Www。",
        ) from exc
    return {
        "kind": kind,
        "period": period,
        "timezone": zone.key,
        "start": utc_iso(start_local.astimezone(timezone.utc)),
        "end": utc_iso(end_local.astimezone(timezone.utc)),
    }


def _overlaps(start: datetime, end: datetime, value: Mapping[str, Any]) -> bool:
    gap_start = parse_time(value.get("start"))
    gap_end = parse_time(value.get("end"))
    return gap_start is not None and gap_end is not None and gap_start < end and gap_end > start


def report_dependency_from_state(
    kb: Path,
    *,
    state: Mapping[str, Any],
    kind: str,
    period: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    window = report_window(
        kind,
        period,
        str(state.get("timezone", "")),
        as_of=now,
    )
    start = parse_time(window["start"])
    end = parse_time(window["end"])
    assert start is not None and end is not None
    blockers = []
    partial_reasons = []
    im_mode = state["grants"]["im"]["mode"]
    if im_mode != "off":
        lane = "discovery" if im_mode == "all_visible" else "monitored"
        if lane == "discovery":
            partial_reasons.append({"kind": "im_discovery_best_effort"})
        cursor_key = f"im:{lane}"
        cursor = state["cursors"].get(cursor_key)
        through = parse_time(cursor.get("through")) if isinstance(cursor, Mapping) else None
        if through is None or through < end:
            blockers.append(
                {
                    "kind": "process",
                    "source": "im",
                    "lane": lane,
                    "start": window["start"],
                    "end": window["end"],
                    "key": f"process:im:{lane}:{window['start']}..{window['end']}",
                }
            )
        for gap in state["gaps"].values():
            if not isinstance(gap, Mapping) or gap.get("lane") != lane:
                continue
            if any(
                isinstance(item, Mapping) and _overlaps(start, end, item)
                for item in gap.get("windows", [])
            ):
                blockers.append(
                    {
                        "kind": "process",
                        "source": "im",
                        "lane": lane,
                        "start": window["start"],
                        "end": window["end"],
                        "key": f"process:im:{lane}:{window['start']}..{window['end']}",
                    }
                )
                break
    try:
        profiles = list_profiles(kb)
    except SourceProfileError as exc:
        profiles = []
        partial_reasons.append(
            {"kind": "source_profile_invalid", "error_code": exc.code}
        )
    unsupported = sorted(
        {
            profile["source_type"]
            for profile in profiles
            if profile["routine"]["enabled"]
            and profile["source_type"] != "feishu_chat"
        }
    )
    if unsupported:
        partial_reasons.append(
            {"kind": "unsupported_routine_sources", "source_types": unsupported}
        )
    unique = {blocker["key"]: blocker for blocker in blockers}
    return {
        "status": "blocked" if unique else ("partial" if partial_reasons else "covered"),
        "window": window,
        "blockers": list(unique.values()),
        "partial_reasons": partial_reasons,
    }


def report_dependency(kb: Path, *, kind: str, period: str) -> dict[str, Any]:
    current = _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
    return report_dependency_from_state(
        kb,
        state=state,
        kind=kind,
        period=period,
        now=current,
    )


def report_migration_readiness(kb: Path) -> dict[str, Any]:
    daily = report_dependency(kb, kind="daily", period=date.today().isoformat())
    unsupported = [
        reason
        for reason in daily["partial_reasons"]
        if reason.get("kind")
        in {"unsupported_routine_sources", "source_profile_invalid"}
    ]
    return {
        "ready": not unsupported,
        "unsupported": unsupported,
    }


def prepare_report_packet(
    kb: Path,
    *,
    kind: str,
    period: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    dependency = report_dependency(kb, kind=kind, period=period)
    if dependency["status"] == "blocked":
        raise DreamingError(
            "DREAMING_REPORT_BLOCKED",
            "报告窗口 coverage 尚未完成。",
            details={"blockers": dependency["blockers"]},
        )
    window = dependency["window"]
    start = parse_time(window["start"])
    end = parse_time(window["end"])
    assert start is not None and end is not None
    findings = []
    for finding in load_findings(kb)["findings"].values():
        updated = parse_time(finding.get("updated_at"))
        if updated is not None and start <= updated < end:
            findings.append(finding)
    findings.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
    packet = {
        "schema_version": "byteworker-report-packet/v1",
        "kind": kind,
        "period": period,
        "window": window,
        "coverage": {
            "status": dependency["status"],
            "partial_reasons": dependency["partial_reasons"],
        },
        "findings": findings,
        "kb_inputs": {
            "todo": "todo.md",
            "context_intent": "report",
            "query_required": True,
        },
        "template": f"templates/report-{kind}.md",
        "citation_policy": "references/citations.md",
        "created_at": utc_iso(current),
    }
    packet_dir = secure_path(kb, "reports", f"{kind}-{period}")
    packet_dir.mkdir(parents=True, mode=0o700)
    packet_path = packet_dir / "packet.json"
    atomic_write_json(packet_path, packet)
    return {
        "status": "prepared",
        "kind": kind,
        "period": period,
        "packet_path": str(packet_path.relative_to(kb.resolve())),
        "finding_count": len(findings),
        "coverage": packet["coverage"],
    }


def refresh_report_dependencies(
    kb: Path,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or _now()
    cleared = 0
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        for key, previous in list(state["report_dependencies"].items()):
            if not isinstance(previous, Mapping):
                continue
            window = previous.get("window")
            blockers = previous.get("blockers")
            if not isinstance(window, Mapping) or not isinstance(blockers, list):
                continue
            end = parse_time(window.get("end"))
            start = parse_time(window.get("start"))
            unresolved = []
            for blocker in blockers:
                if not isinstance(blocker, Mapping) or blocker.get("source") != "im":
                    unresolved.append(blocker)
                    continue
                cursor = state["cursors"].get(f"im:{blocker.get('lane')}")
                through = (
                    parse_time(cursor.get("through"))
                    if isinstance(cursor, Mapping)
                    else None
                )
                if end is None or through is None or through < end:
                    unresolved.append(blocker)
                    continue
                if start is not None and any(
                    isinstance(gap, Mapping)
                    and gap.get("lane") == blocker.get("lane")
                    and any(
                        isinstance(item, Mapping)
                        and _overlaps(start, end, item)
                        for item in gap.get("windows", [])
                    )
                    for gap in state["gaps"].values()
                ):
                    unresolved.append(blocker)
            if not unresolved:
                state["report_dependencies"].pop(key, None)
                kind = str(previous.get("window", {}).get("kind", ""))
                job = state["jobs"].get(kind)
                if isinstance(job, dict):
                    job["blocked_by"] = []
                cleared += 1
            else:
                previous["blockers"] = unresolved
        if cleared:
            state["updated_at"] = utc_iso(current)
            save_state_unlocked(kb, state)
    return {"cleared": cleared}


def enqueue_delivery(
    kb: Path,
    *,
    kind: str,
    period: str,
    report_path: str,
    commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    if kind not in REPORT_KINDS or not report_path.startswith(f"reports/{kind}/"):
        raise DreamingError("DREAMING_REPORT_INVALID", "报告路径与 kind 不一致。")
    outbox_id = "OUT-" + uuid.uuid4().hex
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        state.setdefault("outbox", {})[outbox_id] = {
            "kind": kind,
            "period": period,
            "report_path": report_path,
            "commit": commit,
            "status": "pending",
            "created_at": utc_iso(current),
        }
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"outbox_id": outbox_id, "status": "pending"}


def complete_delivery(
    kb: Path,
    *,
    outbox_id: str,
    delivery_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    if not delivery_id.strip():
        raise DreamingError("DREAMING_DELIVERY_INVALID", "delivery_id 不能为空。")
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        item = state.setdefault("outbox", {}).get(outbox_id)
        if not isinstance(item, dict):
            raise DreamingError("DREAMING_DELIVERY_NOT_FOUND", "outbox 不存在。")
        item["status"] = "delivered"
        item["delivery_id"] = delivery_id.strip()
        item["delivered_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"outbox_id": outbox_id, "status": "delivered"}
