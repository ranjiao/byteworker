"""Provider-neutral planning for Dreaming evidence collection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_batch import create_collected_batch, gc_spool
from dreaming_collectors.feishu_im import FeishuImCollector
from dreaming_grants import (
    close_foreground_session,
    create_foreground_session,
    foreground_session,
    get_im_grant,
)
from dreaming_state import (
    DreamingError,
    load_state_unlocked,
    parse_time,
    save_state_unlocked,
    state_lock,
    utc_iso,
)
from source_profiles import list_profiles


def _window(start: str, end: str) -> tuple[datetime, datetime]:
    start_at = parse_time(start)
    end_at = parse_time(end)
    if start_at is None or end_at is None or start_at >= end_at:
        raise DreamingError(
            "DREAMING_WINDOW_INVALID",
            "start/end 必须是带时区且 start < end 的 ISO 时间。",
        )
    return start_at, end_at


def _split_gap(start: datetime, end: datetime) -> list[dict[str, str]]:
    middle = start + (end - start) / 2
    return [
        {
            "start": utc_iso(start),
            "end": utc_iso(middle),
            "reason": "window_budget",
        },
        {
            "start": utc_iso(middle),
            "end": utc_iso(end),
            "reason": "window_budget",
        },
    ]


def _dedupe(messages: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for value in messages:
        message_id = str(value.get("message_id", "")).strip()
        revision = str(value.get("update_time") or value.get("create_time") or "")
        if message_id:
            result[(message_id, revision)] = dict(value)
    return sorted(
        result.values(),
        key=lambda item: (
            str(item.get("create_time", "")),
            str(item.get("message_id", "")),
        ),
    )


def prepare_im_batch(
    kb: Path,
    *,
    start: str,
    end: str,
    collector: FeishuImCollector | None = None,
    foreground_token: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    start_at, end_at = _window(start, end)
    current = now or datetime.now(timezone.utc)
    if end_at > current:
        raise DreamingError(
            "DREAMING_WINDOW_INVALID",
            "采集窗口 end 不能晚于当前时间。",
        )
    grant = get_im_grant(kb)
    foreground = (
        foreground_session(kb, token=foreground_token, now=current)
        if foreground_token
        else None
    )
    effective_mode = foreground["mode"] if foreground else grant["mode"]
    if effective_mode == "off":
        raise DreamingError(
            "DREAMING_GRANT_REQUIRED",
            "IM grant 当前为 off。",
        )
    gc_spool(
        kb,
        ttl_hours=24 if effective_mode == "all_visible" else 72,
        now=current,
    )
    adapter = collector or FeishuImCollector()
    principal = adapter.principal()
    if effective_mode == "monitored":
        profiles = list_profiles(kb, source_type="feishu_chat")
        chat_ids = sorted(
            {
                str(profile["selector"]["chat_id"])
                for profile in profiles
            }
        )
        if not chat_ids:
            raise DreamingError(
                "DREAMING_MONITORED_SOURCE_EMPTY",
                "没有已登记的 feishu_chat Profile。",
            )
        collected = adapter.collect_monitored(
            chat_ids=chat_ids,
            start=utc_iso(start_at),
            end=utc_iso(end_at),
        )
        source = {
            "source_type": "feishu_chat",
            "principal": principal,
            "lane": "foreground" if foreground else "monitored",
            "collection_mode": "monitored",
            "profile_ids": chat_ids,
        }
    else:
        collected = adapter.collect_discovery(
            start=utc_iso(start_at),
            end=utc_iso(end_at),
        )
        source = {
            "source_type": "feishu_chat",
            "principal": principal,
            "lane": "foreground" if foreground else "discovery",
            "collection_mode": "all_visible",
        }
    coverage = dict(collected["coverage"])
    gaps = list(coverage.get("gaps", []))
    if any(gap.get("kind") == "window_budget" for gap in gaps if isinstance(gap, Mapping)):
        coverage["gaps"] = _split_gap(start_at, end_at)
    messages = _dedupe(collected["messages"])
    receipt = create_collected_batch(
        kb,
        source=source,
        window={
            "requested_start": utc_iso(start_at),
            "requested_end": utc_iso(end_at),
            "observed_start": (
                str(messages[0].get("create_time", "")) if messages else ""
            ),
            "observed_end": (
                str(messages[-1].get("create_time", "")) if messages else ""
            ),
        },
        coverage=coverage,
        messages=messages,
        grant_revision=grant["revision"],
        foreground_token=foreground_token,
        now=current,
    )
    if coverage.get("gaps"):
        with state_lock(kb):
            state = load_state_unlocked(kb, current)
            state["gaps"][receipt["batch_id"]] = {
                "source_type": "feishu_chat",
                "lane": source["lane"],
                "windows": coverage["gaps"],
                "created_at": utc_iso(current),
            }
            state["updated_at"] = utc_iso(current)
            save_state_unlocked(kb, state)
    return {
        **receipt,
        "lane": source["lane"],
        "principal": principal,
        "window": {
            "start": utc_iso(start_at),
            "end": utc_iso(end_at),
        },
    }


def prepare_foreground_im_batch(
    kb: Path,
    *,
    start: str,
    end: str,
    mode: str,
    acknowledge_all_visible: bool,
    collector: FeishuImCollector | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    session = create_foreground_session(
        kb,
        mode=mode,
        acknowledge_all_visible=acknowledge_all_visible,
        now=current,
    )
    try:
        result = prepare_im_batch(
            kb,
            start=start,
            end=end,
            collector=collector,
            foreground_token=session["token"],
            now=current,
        )
    except Exception:
        close_foreground_session(
            kb,
            token=session["token"],
            status="aborted",
            now=current,
        )
        raise
    return {
        **result,
        "foreground": True,
        "session_expires_at": session["expires_at"],
    }
