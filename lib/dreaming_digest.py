"""Routine source inventory, batch preparation and coverage checkpoints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from frontmatter import parse_file
from operation_trace import DIGEST_RUN_ID
from routine_identity import IdentityKey, RoutineDocumentAliases
from source_profile_contract import SourceProfileError
from source_profiles import list_profiles, profile_revision
from sources.adapters.text import chat_window
from sources.registry import create_default_registry
from dreaming_digest_receipts import SUCCESS_STATUSES, recover_linked_receipt
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


BATCH_SCHEMA = "byteworker-dreaming-digest-batch/v1"
ROUTINE_CADENCES = {"daily", "weekly", "monthly"}
EXECUTION_REQUIRED = "DREAMING_DIGEST_EXECUTION_REQUIRED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_key(source_type: str, source_uid: str) -> str:
    value = f"{source_type}\0{source_uid}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _legacy_identity_keys(frontmatter: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    source_type = str(frontmatter.get("source_type", "")).strip()
    keys = []
    source_url = str(frontmatter.get("source_url", "")).strip()
    if source_type and source_url:
        keys.append((source_type, "source_url", source_url))
    source_chat_id = str(frontmatter.get("source_chat_id", "")).strip()
    if source_type == "feishu_chat" and source_chat_id:
        keys.append((source_type, "source_chat_id", source_chat_id))
    return keys


def _recover_legacy_uid(
    candidates: list[tuple[datetime | None, str]],
) -> str:
    """Recover only an unambiguous identity; filenames do not establish recency."""
    uids = {uid for _, uid in candidates}
    if len(uids) == 1:
        return next(iter(uids))
    dated = [(ingested, uid) for ingested, uid in candidates if ingested is not None]
    if not dated or len(dated) != len(candidates):
        return ""
    latest = max(ingested for ingested, _ in dated)
    latest_uids = {uid for ingested, uid in dated if ingested == latest}
    return next(iter(latest_uids)) if len(latest_uids) == 1 else ""


def routine_inventory(kb: Path) -> list[dict[str, Any]]:
    try:
        profiles = list_profiles(kb)
    except SourceProfileError as exc:
        raise DreamingError(
            "SOURCE_PROFILE_INVALID",
            "无法读取定期摄取来源。",
            details={"error_code": exc.code},
        ) from exc
    raw_records = [
        (path, parse_file(str(path))[0])
        for path in sorted((kb / "raw_data").glob("*.md"))
    ]
    try:
        aliases = RoutineDocumentAliases((fm for _, fm in raw_records), profiles)
    except ValueError as exc:
        raise DreamingError("DREAMING_ROUTINE_SOURCE_INVALID", str(exc)) from exc
    document_profile_keys = {aliases.key(profile) for profile in profiles}
    result = []
    profile_uids = {str(profile["source_uid"]) for profile in profiles}
    for profile in profiles:
        routine = profile["routine"]
        if not routine["enabled"]:
            continue
        source_type = str(profile["source_type"])
        source_uid = str(profile["source_uid"])
        result.append(
            {
                "source_key": _source_key(source_type, source_uid),
                "source_type": source_type,
                "source_uid": source_uid,
                "profile_revision": profile_revision(profile),
                "label": str(profile.get("title", ""))[:200],
                "cadence": routine["cadence"],
                "workflow": (
                    "wiki_scan_then_digest"
                    if source_type == "feishu_wiki"
                    else "ordinary_digest"
                ),
                "origin": "profile",
                **({"chat_overlap_seconds": profile["capture_policy"]["overlap_seconds"]}
                   if source_type == "feishu_chat" and profile["capture_policy"]["since_last"]
                   and not profile["capture_policy"]["end"] else {}),
            }
        )
    identity_uids: dict[tuple[str, str, str], list[tuple[datetime | None, str]]] = {}
    document_uids: dict[IdentityKey, list[tuple[datetime | None, str]]] = {}
    scheduled_document_uids: dict[IdentityKey, list[tuple[datetime | None, str]]] = {}
    for _, frontmatter in raw_records:
        source_uid = str(frontmatter.get("source_uid") or "").strip()
        if source_uid:
            candidate = (parse_time(frontmatter.get("ingested")), source_uid)
            for identity_key in _legacy_identity_keys(frontmatter):
                identity_uids.setdefault(identity_key, []).append(candidate)
            key = aliases.key(frontmatter)
            if key is not None:
                document_uids.setdefault(key, []).append(candidate)
                if str(frontmatter.get("routine", "")).strip() in ROUTINE_CADENCES:
                    scheduled_document_uids.setdefault(key, []).append(candidate)
    resolved_document_uids = {
        key: _recover_legacy_uid(scheduled_document_uids.get(key, candidates))
        for key, candidates in document_uids.items()
    }
    merged_documents = {
        key for key, candidates in document_uids.items()
        if len({uid for _, uid in candidates}) > 1
    }

    legacy: dict[tuple[str, str], dict[str, Any]] = {}
    supported_legacy_types = set(create_default_registry().source_types())
    for raw_path, frontmatter in raw_records:
        cadence = str(frontmatter.get("routine", "")).strip()
        source_type = str(frontmatter.get("source_type", "")).strip()
        source_uid = str(frontmatter.get("source_uid") or "").strip()
        if cadence not in ROUTINE_CADENCES:
            continue
        document_key = aliases.key(frontmatter)
        if document_key is not None:
            if document_key in document_profile_keys:
                continue
            chosen_uid = resolved_document_uids.get(document_key, "")
            if chosen_uid and document_key in scheduled_document_uids and source_uid != chosen_uid:
                continue
            source_uid = chosen_uid
        elif source_type and not source_uid:
            candidates = [
                candidate
                for identity_key in _legacy_identity_keys(frontmatter)
                for candidate in identity_uids.get(identity_key, [])
            ]
            source_uid = _recover_legacy_uid(candidates)
        if not source_type or not source_uid:
            raise DreamingError(
                "DREAMING_ROUTINE_SOURCE_INVALID",
                "历史定期来源缺少稳定 source_type/source_uid，且无法从同源归档唯一恢复。",
                details={"raw_path": str(raw_path.relative_to(kb))},
            )
        if source_uid in profile_uids:
            continue
        if source_type not in supported_legacy_types:
            raise DreamingError(
                "DREAMING_ROUTINE_SOURCE_UNSUPPORTED",
                "历史定期来源没有可重放的普通 digest adapter。",
                details={
                    "source_type": source_type,
                    "raw_path": str(raw_path.relative_to(kb)),
                },
            )
        relative_path = str(raw_path.relative_to(kb))
        identity = {
            "source_type": source_type,
            "source_uid": source_uid,
            "source_url": str(frontmatter.get("source_url", "")).strip(),
            "cadence": cadence,
        }
        # A new archive of a merged document need not invalidate the plan.
        if document_key not in merged_documents:
            identity["raw_path"] = relative_path
        revision = "sha256:" + hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        key = (source_type, source_uid)
        candidate = {
            "source_key": _source_key(source_type, source_uid),
            "source_type": source_type,
            "source_uid": source_uid,
            "profile_revision": revision,
            "label": str(frontmatter.get("source_title", ""))[:200],
            "cadence": cadence,
            "workflow": "ordinary_digest",
            "origin": "legacy_raw",
            "raw_path": relative_path,
        }
        previous = legacy.get(key)
        if previous is None or relative_path > previous["raw_path"]:
            legacy[key] = candidate
    result.extend(legacy.values())
    return sorted(result, key=lambda item: (item["source_type"], item["source_uid"]))


def live_process_lease(
    state: Mapping[str, Any],
    *,
    token: str,
    now: datetime,
) -> Mapping[str, Any]:
    lease = state.get("active_lease")
    expires_at = parse_time(lease.get("expires_at")) if isinstance(lease, Mapping) else None
    if (
        not isinstance(lease, Mapping)
        or lease.get("token") != token
        or lease.get("job") != "process"
        or expires_at is None
        or expires_at <= now
    ):
        raise DreamingError(
            "DREAMING_LEASE_MISMATCH",
            "当前没有匹配且有效的 process 租约。",
        )
    return lease


def routine_batch_path(kb: Path, run_id: str) -> Path:
    return secure_path(kb, "digest-batches", f"{run_id}.json")


def load_routine_batch(kb: Path, run_id: str) -> dict[str, Any]:
    path = routine_batch_path(kb, run_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_DIGEST_BATCH_NOT_FOUND",
            "当前 process 的定期摄取批次不存在或损坏。",
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != BATCH_SCHEMA
        or value.get("run_id") != run_id
        or not isinstance(value.get("sources"), dict)
    ):
        raise DreamingError(
            "DREAMING_DIGEST_BATCH_INVALID",
            "定期摄取批次结构无效。",
        )
    return value


def _covered_until(checkpoint, revision, start):
    if not revision or not isinstance(checkpoint, Mapping) or checkpoint.get("profile_revision") != revision:
        return None
    if "covered_windows" not in checkpoint:
        return parse_time(checkpoint.get("through"))
    through = parse_time(checkpoint.get("legacy_through"))
    for window in checkpoint["covered_windows"]:
        left, right = chat_window(window)
        if left <= max(start, through or start):
            through = max(right, through or right)
    return through


def source_covers_window(checkpoint, source, start, end):
    through = _covered_until(checkpoint, source.get("profile_revision"), start)
    return through is not None and through >= end


def _batch_source(source, checkpoint, start, end):
    covered = source_covers_window(checkpoint, source, start, end)
    row = {**source, "status": "covered" if covered else "pending", "receipt": {}, "error_code": ""}
    if not covered and "chat_overlap_seconds" in source and start < end:
        through = _covered_until(checkpoint, source["profile_revision"], start)
        overlap = timedelta(seconds=source["chat_overlap_seconds"])
        left = max(start, through - overlap) if through else start
        row["capture_window"] = f"{utc_iso(left.replace(microsecond=0))}..{utc_iso(end)}"
    return row


def _record_chat_window(previous, checkpoint, window):
    """Keep verified disjoint intervals when newer chat messages were digested first."""
    old = previous if previous.get("profile_revision") == checkpoint["profile_revision"] else {}
    windows = [chat_window(item) for item in old.get("covered_windows", [])]
    windows.append(chat_window(window))
    merged = []
    for left, right in sorted(windows):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    checkpoint["covered_windows"] = [f"{utc_iso(left)}..{utc_iso(right)}" for left, right in merged]
    checkpoint["legacy_through"] = old.get("legacy_through") if "covered_windows" in old else old.get("through")
    times = [merged[-1][1], parse_time(checkpoint["through"]), parse_time(old.get("through"))]
    checkpoint["through"] = utc_iso(max(value for value in times if value is not None))


def prepare_routine_digest(
    kb: Path,
    *,
    token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = live_process_lease(state, token=token, now=current)
        run_id = str(lease["run_id"])
        target_through = lease.get("target_acquired_at") or lease.get("acquired_at") or utc_iso(current)
        dependency = lease.get("dependency")
        if isinstance(dependency, Mapping) and (not lease.get("recent_start") or lease.get("report_target")):
            target_through = dependency.get("end") or target_through
        if lease.get("recent_start"):
            target_through = utc_iso(parse_time(target_through).replace(microsecond=0))
        target_start = lease.get("recent_start") or (dependency or {}).get("start") or target_through
        path = routine_batch_path(kb, run_id)
        if path.is_file():
            batch = load_routine_batch(kb, run_id)
        else:
            inventory = routine_inventory(kb)
            checkpoints = state.get("source_checkpoints", {})
            batch = {
                "schema_version": BATCH_SCHEMA,
                "run_id": run_id,
                "lease_epoch": lease["epoch"],
                "target_through": target_through,
                "target_start": target_start,
                "prepared_at": utc_iso(current),
                "status": "running",
                "sources": {
                    item["source_key"]: _batch_source(
                        item, checkpoints.get(item["source_key"]), parse_time(target_start), parse_time(target_through),
                    )
                    for item in inventory
                },
            }
            resume_from = lease.get("resume_from")
            if resume_from:
                previous = load_routine_batch(kb, resume_from)
                batch["target_start"] = previous["target_start"]
                batch["target_through"] = previous["target_through"]
                start = parse_time(batch["target_start"])
                end = parse_time(batch["target_through"])
                batch["sources"] = {}
                for item in inventory:
                    key = item["source_key"]
                    row = _batch_source(item, checkpoints.get(key), start, end)
                    old = previous["sources"].get(key) or {}
                    if row["status"] == "pending" and old.get("profile_revision") == row["profile_revision"]:
                        if old.get("work"):
                            row["work"] = old["work"]
                            # Reuse the exact capture window that the linked receipt must cover.
                            if old.get("capture_window"):
                                row["capture_window"] = old["capture_window"]
                        if old.get("status") in SUCCESS_STATUSES - {"covered"}:
                            row.update(status=old["status"], receipt=old["receipt"])
                        recover_linked_receipt(kb, row, batch, lease, current)
                    batch["sources"][key] = row
            atomic_write_json(path, batch)
            if resume_from:
                for row in batch["sources"].values():
                    advance_source_checkpoint(state, row, batch, current)
                save_state_unlocked(kb, state)

    return {
        "schema_version": BATCH_SCHEMA,
        "run_id": run_id,
        "status": batch["status"],
        "target_through": batch["target_through"],
        "sources": [
            {
                **{key: item[key] for key in (
                    "source_key", "source_type", "source_uid", "profile_revision",
                    "cadence", "workflow", "origin", "status",
                )},
                "label": item.get("label", ""),
                **{key: item[key] for key in ("work", "raw_path", "error_code", "capture_window") if item.get(key)},
            }
            for item in batch["sources"].values()
        ],
        "source_count": len(batch["sources"]),
    }


def attach_digest_run(kb: Path, *, token: str, source_key: str, digest_run_id: str) -> dict[str, Any]:
    if not DIGEST_RUN_ID.fullmatch(digest_run_id):
        raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "Digest 运行编号无效。")
    current = _now()
    with state_lock(kb):
        lease = live_process_lease(load_state_unlocked(kb, current), token=token, now=current)
        batch = load_routine_batch(kb, str(lease["run_id"]))
        source = batch["sources"].get(source_key)
        if batch["status"] != "running" or not source or source["status"] != "pending":
            raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "来源不属于当前待处理批次。")
        work = {"digest_run_id": digest_run_id}
        if source.get("work", work) != work:
            raise DreamingError("DREAMING_DIGEST_RESULT_CONFLICT", "该来源已有进行中的 Digest。")
        source["work"] = work
        atomic_write_json(routine_batch_path(kb, str(lease["run_id"])), batch)
    return work


def advance_source_checkpoint(state: dict[str, Any], source: Mapping[str, Any],
                               batch: Mapping[str, Any], current: datetime) -> None:
    key = source["source_key"]
    start = parse_time(batch.get("target_start") or batch["target_through"])
    end = parse_time(batch["target_through"])
    checkpoints = state.setdefault("source_checkpoints", {})
    if source["status"] == "covered" or source["status"] not in SUCCESS_STATUSES:
        return
    previous = checkpoints.get(key, {})
    if source_covers_window(previous, source, start, end):
        return
    checkpoints[key] = {
        "source_type": source["source_type"],
        "source_uid": source["source_uid"],
        "profile_revision": source["profile_revision"],
        "through": batch["target_through"],
        "run_id": batch["run_id"],
        "result_status": source["status"],
        "updated_at": utc_iso(current),
    }
    receipt = source["receipt"]
    if source.get("capture_window") or (previous.get("covered_windows") and receipt.get("window_start")):
        _record_chat_window(previous, checkpoints[key], f"{receipt['window_start']}..{receipt['window_end']}")
    if source["status"] == "empty_window":
        checkpoints[key]["digest_run_id"] = source["receipt"]["digest_run_id"]
    elif source["workflow"] == "ordinary_digest":
        checkpoints[key]["digest_key"] = source["receipt"]["digest_key"]
    else:
        checkpoints[key]["tree_hash"] = source["receipt"]["tree_hash"]


def process_has_complete_routine_digest(
    kb: Path,
    *,
    run_id: str,
) -> bool:
    if not routine_inventory(kb):
        return True
    try:
        batch = load_routine_batch(kb, run_id)
    except DreamingError:
        return False
    return batch.get("status") == "completed"


def require_routine_digest_completion(
    kb: Path, lease: Mapping[str, Any],
) -> None:
    run_id = lease.get("run_id")
    if not run_id or not routine_batch_path(kb, run_id).is_file():
        return
    batch = load_routine_batch(kb, run_id)
    if batch["status"] == "running":
        raise DreamingError(
            EXECUTION_REQUIRED, "定期摄取批次仍在运行，请先登记来源结果并汇总批次。",
        )
