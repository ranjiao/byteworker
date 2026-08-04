"""Explicit Dreaming grants and revocation cleanup."""

from __future__ import annotations

import shutil
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_state import (
    DreamingError,
    load_state_unlocked,
    save_state_unlocked,
    secure_path,
    state_lock,
    utc_iso,
    parse_time,
)


IM_MODES = {"off", "monitored", "all_visible"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_im_grant(kb: Path) -> dict[str, Any]:
    current = _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
    grants = state["grants"]
    return {
        "revision": grants["revision"],
        **dict(grants["im"]),
    }


def _cleanup_revoked_state(kb: Path, *, keep_monitored: bool) -> dict[str, Any]:
    removed: dict[str, Any] = {"directories": 0, "files": 0, "batch_ids": set()}
    for name in ("spool", "batches"):
        root = secure_path(kb, name)
        if not root.is_dir():
            continue
        for child in list(root.iterdir()):
            if child.is_symlink():
                raise DreamingError(
                    "DREAMING_STATE_PATH_INVALID",
                    f"撤销清理拒绝符号链接: {child}",
                )
            if keep_monitored:
                manifest = (
                    child / "manifest.json"
                    if name == "batches"
                    else secure_path(kb, "batches", child.name, "manifest.json")
                )
                if manifest.is_file():
                    try:
                        value = json.loads(manifest.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        value = {}
                    source = value.get("source") if isinstance(value, Mapping) else {}
                    if isinstance(source, Mapping) and source.get("lane") == "monitored":
                        continue
            if child.is_dir():
                removed["files"] += sum(1 for path in child.rglob("*") if path.is_file())
                shutil.rmtree(child)
                removed["directories"] += 1
                removed["batch_ids"].add(child.name)
            else:
                child.unlink()
                removed["files"] += 1
    return removed


def set_im_grant(
    kb: Path,
    *,
    mode: str,
    persist_finding: bool,
    acknowledge_all_visible: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = mode.strip()
    if normalized not in IM_MODES:
        raise DreamingError(
            "DREAMING_GRANT_INVALID",
            "IM mode 必须是 off、monitored 或 all_visible。",
        )
    if normalized == "all_visible" and not acknowledge_all_visible:
        raise DreamingError(
            "DREAMING_GRANT_ACK_REQUIRED",
            "all_visible 会扫描 P2P 和免打扰会话，必须显式确认。",
        )
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        grants = state["grants"]
        old = dict(grants["im"])
        revision = int(grants["revision"]) + 1
        grants["revision"] = revision
        grants["im"] = {
            "mode": normalized,
            "persist_finding": bool(persist_finding),
            "updated_at": utc_iso(current),
        }
        cleanup: dict[str, Any] = {
            "directories": 0,
            "files": 0,
            "batch_ids": set(),
        }
        if old.get("mode") == "all_visible" and normalized != "all_visible":
            cleanup = _cleanup_revoked_state(
                kb,
                keep_monitored=normalized == "monitored",
            )
        elif normalized == "off":
            cleanup = _cleanup_revoked_state(kb, keep_monitored=False)
        for batch_id in cleanup["batch_ids"]:
            run = state["runs"].get(batch_id)
            if isinstance(run, dict):
                run.update(
                    {
                        "stage": "revoked",
                        "manifest": "",
                        "error_code": "DREAMING_GRANT_REVOKED",
                        "updated_at": utc_iso(current),
                    }
                )
            state["gaps"].pop(batch_id, None)
        if cleanup["batch_ids"]:
            from dreaming_consolidation import purge_findings_for_batches_unlocked

            purge_findings_for_batches_unlocked(
                kb,
                batch_ids=set(cleanup["batch_ids"]),
                now=current,
            )
        if state["actions"]:
            from dreaming_action_ledger import (
                invalidate_actions_for_grant_change_unlocked,
            )

            invalidate_actions_for_grant_change_unlocked(
                kb,
                state=state,
                now=current,
            )
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {
        "revision": revision,
        **dict(grants["im"]),
        "cleanup": {
            "directories": cleanup["directories"],
            "files": cleanup["files"],
        },
    }


def set_action_grants(
    kb: Path,
    *,
    persist_report: bool,
    archive: bool,
    instant_alert: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        grants = state["grants"]
        revision = int(grants["revision"]) + 1
        value = {
            "persist_report": bool(persist_report),
            "archive": bool(archive),
            "instant_alert": bool(instant_alert),
            "updated_at": utc_iso(current),
        }
        grants["revision"] = revision
        grants["actions"] = value
        if state["actions"]:
            from dreaming_action_ledger import (
                invalidate_actions_for_grant_change_unlocked,
            )

            invalidate_actions_for_grant_change_unlocked(
                kb,
                state=state,
                now=current,
            )
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"revision": revision, **value}


def create_foreground_session(
    kb: Path,
    *,
    mode: str,
    acknowledge_all_visible: bool,
    ttl_seconds: int = 7200,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = mode.strip()
    if normalized not in {"monitored", "all_visible"}:
        raise DreamingError(
            "DREAMING_GRANT_INVALID",
            "foreground mode 必须是 monitored 或 all_visible。",
        )
    if normalized == "all_visible" and not acknowledge_all_visible:
        raise DreamingError(
            "DREAMING_GRANT_ACK_REQUIRED",
            "foreground all_visible 会扫描 P2P 和免打扰会话，必须显式确认。",
        )
    if ttl_seconds <= 0 or ttl_seconds > 86400:
        raise DreamingError(
            "DREAMING_GRANT_INVALID",
            "foreground ttl_seconds 必须在 1..86400。",
        )
    current = now or _now()
    token = uuid.uuid4().hex
    expires_at = current + timedelta(seconds=ttl_seconds)
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        for session_id, session in list(state["foreground_sessions"].items()):
            expires = (
                parse_time(session.get("expires_at"))
                if isinstance(session, Mapping)
                else None
            )
            if expires is None or expires <= current:
                state["foreground_sessions"].pop(session_id, None)
        state["foreground_sessions"][token] = {
            "mode": normalized,
            "persist_finding": False,
            "created_at": utc_iso(current),
            "expires_at": utc_iso(expires_at),
            "status": "active",
        }
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {
        "token": token,
        "mode": normalized,
        "persist_finding": False,
        "expires_at": utc_iso(expires_at),
    }


def foreground_session(
    kb: Path,
    *,
    token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        value = state["foreground_sessions"].get(token)
    expires = parse_time(value.get("expires_at")) if isinstance(value, Mapping) else None
    if (
        not isinstance(value, Mapping)
        or value.get("status") != "active"
        or expires is None
        or expires <= current
    ):
        raise DreamingError(
            "DREAMING_FOREGROUND_SESSION_INVALID",
            "foreground session 不存在或已过期。",
        )
    return {"token": token, **dict(value)}


def close_foreground_session(
    kb: Path,
    *,
    token: str,
    status: str,
    now: datetime | None = None,
) -> None:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        value = state["foreground_sessions"].get(token)
        if isinstance(value, dict):
            value["status"] = status
            value["closed_at"] = utc_iso(current)
            state["updated_at"] = utc_iso(current)
            save_state_unlocked(kb, state)


def expire_foreground_sessions(
    kb: Path,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or _now()
    expired = 0
    aborted_batches = 0
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        expired_tokens = set()
        for token, session in state["foreground_sessions"].items():
            expires = (
                parse_time(session.get("expires_at"))
                if isinstance(session, Mapping)
                else None
            )
            if (
                isinstance(session, dict)
                and session.get("status") == "active"
                and (expires is None or expires <= current)
            ):
                session["status"] = "expired"
                session["closed_at"] = utc_iso(current)
                expired_tokens.add(token)
                expired += 1
        for run in state["runs"].values():
            if (
                isinstance(run, dict)
                and run.get("foreground_token") in expired_tokens
                and run.get("stage") not in {"committed", "aborted", "revoked"}
            ):
                run["stage"] = "aborted"
                run["error_code"] = "DREAMING_FOREGROUND_SESSION_EXPIRED"
                run["updated_at"] = utc_iso(current)
                aborted_batches += 1
        if expired:
            state["updated_at"] = utc_iso(current)
            save_state_unlocked(kb, state)
    return {"expired_sessions": expired, "aborted_batches": aborted_batches}
