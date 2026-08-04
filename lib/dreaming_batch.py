"""Crash-recoverable Dreaming batch and cursor protocol."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_models import validate_dreaming_batch, validate_evidence_batch
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _batch_dir(kb: Path, batch_id: str) -> Path:
    if not batch_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in batch_id):
        raise DreamingError("DREAMING_BATCH_INVALID", "batch_id 含非法字符。")
    return secure_path(kb, "batches", batch_id)


def create_collected_batch(
    kb: Path,
    *,
    source: Mapping[str, Any],
    window: Mapping[str, str],
    coverage: Mapping[str, Any],
    messages: list[Mapping[str, Any]],
    grant_revision: int,
    foreground_token: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    batch_id = "EB-" + uuid.uuid4().hex
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        grants = state["grants"]
        if grants["revision"] != grant_revision:
            raise DreamingError(
                "DREAMING_GRANT_STALE",
                "IM grant 在采集期间发生变化，拒绝提交 batch。",
            )
        im_grant = grants["im"]
        lane = str(source.get("lane", ""))
        mode = im_grant["mode"]
        if lane == "foreground":
            session = state["foreground_sessions"].get(foreground_token)
            expires = parse_time(session.get("expires_at")) if isinstance(session, Mapping) else None
            if (
                not isinstance(session, Mapping)
                or session.get("status") != "active"
                or expires is None
                or expires <= current
                or source.get("collection_mode") != session.get("mode")
            ):
                raise DreamingError(
                    "DREAMING_FOREGROUND_SESSION_INVALID",
                    "foreground session 不存在、已过期或 mode 不匹配。",
                )
        elif mode == "off" or (lane == "discovery" and mode != "all_visible"):
            raise DreamingError(
                "DREAMING_GRANT_REQUIRED",
                "当前 IM grant 不允许提交该采集 lane。",
            )
        batch_dir = _batch_dir(kb, batch_id)
        spool_dir = secure_path(kb, "spool", batch_id)
        batch_dir.mkdir(parents=True, mode=0o700)
        spool_dir.mkdir(parents=True, mode=0o700)
        os.chmod(batch_dir, 0o700)
        os.chmod(spool_dir, 0o700)
        items = []
        for message in messages:
            message_id = str(message.get("message_id", "")).strip()
            chat_id = str(message.get("chat_id", "")).strip()
            if not message_id or not chat_id:
                continue
            revision = str(message.get("update_time") or message.get("create_time") or "")
            payload = dict(message)
            name = hashlib.sha256(
                f"{chat_id}:{message_id}:{revision}".encode("utf-8")
            ).hexdigest() + ".json"
            content_path = spool_dir / name
            atomic_write_json(content_path, payload)
            items.append(
                {
                    "item_id": f"message:{message_id}",
                    "occurred_at": str(message.get("create_time", "")),
                    "author_ref": str(
                        (message.get("sender") or {}).get("id", "")
                        if isinstance(message.get("sender"), Mapping)
                        else ""
                    ),
                    "thread_ref": str(message.get("thread_id", "")),
                    "anchor": {"kind": "message_id", "value": message_id},
                    "content_ref": f"spool://{batch_id}/{name}",
                    "content_sha256": _sha(payload),
                    "revision": revision,
                    "chat_id": chat_id,
                }
            )
        evidence = {
            "schema_version": "byteworker-evidence-batch/v1",
            "batch_id": batch_id,
            "source": {**dict(source), "grant_revision": grant_revision},
            "window": dict(window),
            "coverage": dict(coverage),
            "items": items,
            "created_at": utc_iso(current),
        }
        validate_evidence_batch(evidence)
        manifest_path = batch_dir / "manifest.json"
        atomic_write_json(manifest_path, evidence)
        manifest_sha = _sha(evidence)
        batch = {
            "schema_version": "byteworker-dreaming-batch/v1",
            "batch_id": batch_id,
            "stage": "collected",
            "manifest_sha256": manifest_sha,
            "grant_revision": grant_revision,
            "created_at": utc_iso(current),
        }
        validate_dreaming_batch(batch)
        atomic_write_json(batch_dir / "batch.json", batch)
        state["runs"][batch_id] = {
            "stage": "collected",
            "manifest": f"state/dreaming/batches/{batch_id}/manifest.json",
            "grant_revision": grant_revision,
            "foreground_token": foreground_token,
            "updated_at": utc_iso(current),
        }
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {
        "batch_id": batch_id,
        "stage": "collected",
        "manifest_path": f"state/dreaming/batches/{batch_id}/manifest.json",
        "item_count": len(items),
        "coverage": dict(coverage),
    }


def write_stage_receipt(
    kb: Path,
    *,
    batch_id: str,
    stage: str,
    receipt: Mapping[str, Any],
    expected_grant_revision: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    names = {
        "analyzed": "analysis.receipt.json",
        "consolidated": "consolidation.receipt.json",
    }
    if stage not in names:
        raise DreamingError("DREAMING_BATCH_INVALID", f"不支持 stage: {stage}")
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        run = state["runs"].get(batch_id)
        if not isinstance(run, dict):
            raise DreamingError("DREAMING_BATCH_NOT_FOUND", f"batch 不存在: {batch_id}")
        if (
            expected_grant_revision is not None
            and state["grants"]["revision"] != expected_grant_revision
        ):
            raise DreamingError(
                "DREAMING_GRANT_STALE",
                "写 stage receipt 前 grant revision 已变化。",
            )
        batch_dir = _batch_dir(kb, batch_id)
        atomic_write_json(
            batch_dir / names[stage],
            {"batch_id": batch_id, "stage": stage, **dict(receipt)},
        )
        run["stage"] = stage
        run["updated_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"batch_id": batch_id, "stage": stage}


def commit_batch(
    kb: Path,
    *,
    batch_id: str,
    cursor_key: str,
    through: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    if not cursor_key.strip() or not through.strip():
        raise DreamingError(
            "DREAMING_BATCH_INVALID",
            "cursor_key 和 through 不能为空。",
        )
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        run = state["runs"].get(batch_id)
        batch_dir = _batch_dir(kb, batch_id)
        if not isinstance(run, dict) or not (
            batch_dir / "consolidation.receipt.json"
        ).is_file():
            raise DreamingError(
                "DREAMING_BATCH_NOT_READY",
                "batch 尚未完成 consolidation。",
            )
        foreground_token = str(run.get("foreground_token", ""))
        if (
            not foreground_token
            and state["grants"]["revision"] != run.get("grant_revision")
        ):
            raise DreamingError(
                "DREAMING_GRANT_STALE",
                "提交 cursor 前 grant revision 已变化。",
            )
        if foreground_token:
            session = state["foreground_sessions"].get(foreground_token)
            if not isinstance(session, Mapping) or session.get("status") != "active":
                raise DreamingError(
                    "DREAMING_FOREGROUND_SESSION_INVALID",
                    "foreground session 已关闭。",
                )
        marker = {
            "batch_id": batch_id,
            "stage": "committed",
            "cursor_key": cursor_key,
            "through": through,
            "committed_at": utc_iso(current),
        }
        atomic_write_json(batch_dir / "batch.commit.json", marker)
        try:
            manifest = json.loads(
                (batch_dir / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            manifest = {}
        coverage = manifest.get("coverage") if isinstance(manifest, Mapping) else {}
        source = manifest.get("source") if isinstance(manifest, Mapping) else {}
        window = manifest.get("window") if isinstance(manifest, Mapping) else {}
        if (
            isinstance(coverage, Mapping)
            and not coverage.get("gaps")
            and isinstance(source, Mapping)
            and isinstance(window, Mapping)
        ):
            lane = source.get("lane")
            start = str(window.get("requested_start", ""))
            end = str(window.get("requested_end", ""))
            for gap_id, gap in list(state["gaps"].items()):
                if not isinstance(gap, dict) or gap.get("lane") != lane:
                    continue
                remaining = [
                    item
                    for item in gap.get("windows", [])
                    if not (
                        isinstance(item, Mapping)
                        and str(item.get("start", "")) >= start
                        and str(item.get("end", "")) <= end
                    )
                ]
                if remaining:
                    gap["windows"] = remaining
                else:
                    state["gaps"].pop(gap_id, None)
        state["cursors"][cursor_key] = {
            "through": through,
            "committed_batch_id": batch_id,
            "updated_at": utc_iso(current),
        }
        run["stage"] = "committed"
        run["updated_at"] = utc_iso(current)
        state["receipt_index"][batch_id] = marker
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return marker


def recover_committed_cursors(
    kb: Path,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or _now()
    repaired = 0
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        root = secure_path(kb, "batches")
        for marker_path in root.glob("*/batch.commit.json") if root.is_dir() else []:
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = str(marker.get("cursor_key", ""))
            batch_id = str(marker.get("batch_id", ""))
            if not key or not batch_id:
                continue
            cursor = state["cursors"].get(key)
            if not isinstance(cursor, Mapping) or cursor.get("committed_batch_id") != batch_id:
                state["cursors"][key] = {
                    "through": str(marker.get("through", "")),
                    "committed_batch_id": batch_id,
                    "updated_at": utc_iso(current),
                }
                repaired += 1
        if repaired:
            state["updated_at"] = utc_iso(current)
            save_state_unlocked(kb, state)
    return {"repaired": repaired}


def abort_batch(
    kb: Path,
    *,
    batch_id: str,
    error_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        run = state["runs"].get(batch_id)
        if not isinstance(run, dict):
            raise DreamingError("DREAMING_BATCH_NOT_FOUND", f"batch 不存在: {batch_id}")
        run.update(
            {
                "stage": "aborted",
                "error_code": error_code.strip() or "DREAMING_BATCH_ABORTED",
                "updated_at": utc_iso(current),
            }
        )
        foreground_token = str(run.get("foreground_token", ""))
        session = state["foreground_sessions"].get(foreground_token)
        if foreground_token and isinstance(session, dict):
            session["status"] = "aborted"
            session["closed_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"batch_id": batch_id, "stage": "aborted", "error_code": run["error_code"]}


def gc_spool(
    kb: Path,
    *,
    ttl_hours: int,
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or _now()
    cutoff = current - timedelta(hours=max(1, ttl_hours))
    removed = 0
    root = secure_path(kb, "spool")
    if root.is_dir():
        for child in root.iterdir():
            if child.is_symlink():
                raise DreamingError(
                    "DREAMING_STATE_PATH_INVALID",
                    f"spool 不能包含符号链接: {child}",
                )
            modified = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
    return {"removed": removed}
