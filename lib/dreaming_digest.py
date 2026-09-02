"""Dreaming orchestration for replaying ordinary routine digest workflows."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from frontmatter import parse_file
from source_profile_contract import SourceProfileError
from source_profiles import list_profiles, profile_revision
from sources.registry import create_default_registry
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


RESULT_SCHEMA = "byteworker-dreaming-digest-result/v1"
BATCH_SCHEMA = "byteworker-dreaming-digest-batch/v1"
SUCCESS_STATUSES = {"committed", "noop", "observed"}
TRANSACTION_SUCCESS_STATUSES = {"committed", "noop"}
SKILL_ROOT = Path(__file__).resolve().parents[1]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _source_key(source_type: str, source_uid: str) -> str:
    value = f"{source_type}\0{source_uid}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def routine_inventory(kb: Path) -> list[dict[str, Any]]:
    try:
        profiles = list_profiles(kb)
    except SourceProfileError as exc:
        raise DreamingError(
            "SOURCE_PROFILE_INVALID",
            "无法读取定期摄取来源。",
            details={"error_code": exc.code},
        ) from exc
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
                "cadence": routine["cadence"],
                "workflow": (
                    "wiki_scan_then_digest"
                    if source_type == "feishu_wiki"
                    else "ordinary_digest"
                ),
                "origin": "profile",
            }
        )
    legacy: dict[tuple[str, str], dict[str, Any]] = {}
    supported_legacy_types = set(create_default_registry().source_types())
    for raw_path in sorted((kb / "raw_data").glob("*.md")):
        frontmatter, _ = parse_file(str(raw_path))
        cadence = str(frontmatter.get("routine", "")).strip()
        source_type = str(frontmatter.get("source_type", "")).strip()
        source_uid = str(frontmatter.get("source_uid", "")).strip()
        if cadence not in {"daily", "weekly", "monthly"}:
            continue
        if not source_type or not source_uid:
            raise DreamingError(
                "DREAMING_ROUTINE_SOURCE_INVALID",
                "历史定期来源缺少稳定 source_type/source_uid，无法后台重放。",
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
            "raw_path": relative_path,
        }
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


def _live_process_lease(
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


def _batch_path(kb: Path, run_id: str) -> Path:
    return secure_path(kb, "digest-batches", f"{run_id}.json")


def _load_batch(kb: Path, run_id: str) -> dict[str, Any]:
    path = _batch_path(kb, run_id)
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


def prepare_routine_digest(
    kb: Path,
    *,
    token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    inventory = routine_inventory(kb)
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = _live_process_lease(state, token=token, now=current)
        run_id = str(lease["run_id"])
        target_through = str(
            (
                lease.get("dependency")
                if isinstance(lease.get("dependency"), Mapping)
                else {}
            ).get("end")
            or lease.get("acquired_at")
            or utc_iso(current)
        )
        path = _batch_path(kb, run_id)
        if path.is_file():
            batch = _load_batch(kb, run_id)
        else:
            batch = {
                "schema_version": BATCH_SCHEMA,
                "run_id": run_id,
                "lease_epoch": lease["epoch"],
                "target_through": target_through,
                "prepared_at": utc_iso(current),
                "status": "running",
                "sources": {
                    item["source_key"]: {
                        **item,
                        "status": "pending",
                        "receipt": {},
                        "error_code": "",
                    }
                    for item in inventory
                },
            }
            atomic_write_json(path, batch)
    return {
        "schema_version": BATCH_SCHEMA,
        "run_id": run_id,
        "status": batch["status"],
        "target_through": batch["target_through"],
        "sources": [
            {
                key: item[key]
                for key in (
                    "source_key",
                    "source_type",
                    "source_uid",
                    "profile_revision",
                    "cadence",
                    "workflow",
                    "origin",
                    "status",
                )
            }
            for item in batch["sources"].values()
        ],
        "source_count": len(batch["sources"]),
    }


def _load_result(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved == SKILL_ROOT or SKILL_ROOT in resolved.parents:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "普通 digest 结果不得位于 skill 仓库。",
        )
    try:
        if resolved.stat().st_size > 1024 * 1024:
            raise OSError("result too large")
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "无法读取普通 digest 结果。",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != RESULT_SCHEMA:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            f"普通 digest 结果必须使用 {RESULT_SCHEMA}。",
        )
    return value


def _verify_git_commit(kb: Path, commit: str) -> None:
    if not commit:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "committed digest 缺少 commit。",
        )
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=kb,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "digest receipt 引用的 commit 不存在。",
        )


def _verify_transaction_receipt(
    kb: Path,
    source: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    status = str(result.get("status", ""))
    if status not in TRANSACTION_SUCCESS_STATUSES:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "来源结果必须是 committed 或 noop。",
        )
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping) or receipt.get("status") != status:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "来源结果与下游 digest receipt 状态不一致。",
        )
    digest_key = str(receipt.get("digest_key", ""))
    prefix = f"{source['source_type']}:{source['source_uid']}:"
    if not digest_key.startswith(prefix):
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "digest receipt 与来源 identity 不一致。",
        )
    raw_path_value = str(receipt.get("raw_path", ""))
    if raw_path_value:
        raw_path = (kb / raw_path_value).resolve()
    else:
        raw_id = str(receipt.get("raw_id", ""))
        matches = []
        for candidate in (kb / "raw_data").glob("*.md"):
            frontmatter, _ = parse_file(str(candidate))
            if str(frontmatter.get("raw_id", "")) == raw_id:
                matches.append(candidate.resolve())
        if len(matches) != 1:
            raise DreamingError(
                "DREAMING_DIGEST_RESULT_INVALID",
                "noop digest receipt 无法唯一定位已提交 raw。",
            )
        raw_path = matches[0]
    raw_root = (kb / "raw_data").resolve()
    if raw_root not in raw_path.parents or not raw_path.is_file():
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "digest receipt 的 raw_path 无效。",
        )
    frontmatter, _ = parse_file(str(raw_path))
    if (
        str(frontmatter.get("source_type", "")) != source["source_type"]
        or str(frontmatter.get("source_uid", "")) != source["source_uid"]
        or str(frontmatter.get("digest_status", "")) != "digested"
        or str(frontmatter.get("digest_key", "")) != digest_key
    ):
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "digest receipt 与已提交 raw 不一致。",
        )
    if status == "committed":
        _verify_git_commit(kb, str(receipt.get("commit", "")))
    return {
        "status": status,
        "digest_key": digest_key,
        "raw_id": str(frontmatter.get("raw_id", "")),
        "commit": str(receipt.get("commit", "")),
    }


def _verify_wiki_receipt(
    kb: Path,
    source: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    if result.get("status") != "observed":
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "Wiki 定期流程必须返回 observed 扫描结果。",
        )
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping):
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "Wiki 扫描结果缺少 receipt。",
        )
    state_path_value = str(receipt.get("state_path", ""))
    state_path = Path(state_path_value)
    if not state_path.is_absolute():
        state_path = kb / state_path
    state_path = state_path.resolve()
    wiki_root = (kb / "state" / "wiki").resolve()
    if wiki_root not in state_path.parents or not state_path.is_file():
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "Wiki 扫描状态路径无效。",
        )
    try:
        snapshot = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "Wiki 扫描状态损坏。",
        ) from exc
    expected_uid = (
        f"feishu_wiki:{snapshot.get('space', {}).get('space_id', '')}:"
        f"{snapshot.get('scope', {}).get('root_node_token', '')}"
    )
    if (
        expected_uid != source["source_uid"]
        or snapshot.get("tree_hash") != receipt.get("tree_hash")
        or not snapshot.get("coverage", {}).get("complete")
    ):
        raise DreamingError(
            "DREAMING_DIGEST_RESULT_INVALID",
            "Wiki 扫描结果与来源、tree hash 或完整覆盖不一致。",
        )
    return {
        "status": "observed",
        "tree_hash": str(receipt.get("tree_hash", "")),
        "state_path": str(state_path.relative_to(kb)),
        "delta": dict(receipt.get("delta", {})),
    }


def record_routine_digest_result(
    kb: Path,
    *,
    token: str,
    input_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    result = _load_result(input_path)
    source_key = str(result.get("source_key", ""))
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = _live_process_lease(state, token=token, now=current)
        batch = _load_batch(kb, str(lease["run_id"]))
        source = batch["sources"].get(source_key)
        if not isinstance(source, dict):
            raise DreamingError(
                "DREAMING_DIGEST_RESULT_INVALID",
                "结果来源不属于当前定期摄取批次。",
            )
        if (
            result.get("source_type") != source["source_type"]
            or result.get("source_uid") != source["source_uid"]
            or result.get("profile_revision") != source["profile_revision"]
        ):
            raise DreamingError(
                "DREAMING_DIGEST_RESULT_INVALID",
                "结果来源或 Profile revision 与批次快照不一致。",
            )
        verified = (
            _verify_wiki_receipt(kb, source, result)
            if source["workflow"] == "wiki_scan_then_digest"
            else _verify_transaction_receipt(kb, source, result)
        )
        if source["status"] in SUCCESS_STATUSES and source["receipt"] != verified:
            raise DreamingError(
                "DREAMING_DIGEST_RESULT_CONFLICT",
                "同一来源已记录不同 digest receipt。",
            )
        source["status"] = verified["status"]
        source["receipt"] = verified
        source["finished_at"] = utc_iso(current)
        atomic_write_json(_batch_path(kb, str(lease["run_id"])), batch)
    return {
        "run_id": lease["run_id"],
        "source_key": source_key,
        "status": verified["status"],
    }


def complete_routine_digest(
    kb: Path,
    *,
    token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = _live_process_lease(state, token=token, now=current)
        run_id = str(lease["run_id"])
        batch = _load_batch(kb, run_id)
        pending = [
            item["source_key"]
            for item in batch["sources"].values()
            if item.get("status") not in SUCCESS_STATUSES
        ]
        if pending:
            raise DreamingError(
                "DREAMING_DIGEST_INCOMPLETE",
                "仍有定期来源未取得 committed/noop 回执。",
                details={"pending_source_keys": pending},
            )
        current_inventory = {
            item["source_key"]: item for item in routine_inventory(kb)
        }
        expected_inventory = {
            key: {
                "source_type": item["source_type"],
                "source_uid": item["source_uid"],
                "profile_revision": item["profile_revision"],
            }
            for key, item in batch["sources"].items()
        }
        actual_inventory = {
            key: {
                "source_type": item["source_type"],
                "source_uid": item["source_uid"],
                "profile_revision": item["profile_revision"],
            }
            for key, item in current_inventory.items()
        }
        if expected_inventory != actual_inventory:
            raise DreamingError(
                "DREAMING_DIGEST_INVENTORY_CHANGED",
                "定期来源清单在本轮运行中发生变化，拒绝推进覆盖检查点。",
            )
        checkpoints = state.setdefault("source_checkpoints", {})
        for key, source in batch["sources"].items():
            checkpoints[key] = {
                "source_type": source["source_type"],
                "source_uid": source["source_uid"],
                "profile_revision": source["profile_revision"],
                "through": batch["target_through"],
                "run_id": run_id,
                "result_status": source["status"],
                "updated_at": utc_iso(current),
            }
            if source["workflow"] == "ordinary_digest":
                checkpoints[key]["digest_key"] = source["receipt"]["digest_key"]
            else:
                checkpoints[key]["tree_hash"] = source["receipt"]["tree_hash"]
        batch["status"] = "completed"
        batch["completed_at"] = utc_iso(current)
        atomic_write_json(_batch_path(kb, run_id), batch)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    from dreaming_reports import refresh_report_dependencies

    refresh_report_dependencies(kb, now=current)
    return {
        "schema_version": BATCH_SCHEMA,
        "run_id": run_id,
        "status": "completed",
        "target_through": batch["target_through"],
        "source_count": len(batch["sources"]),
        "committed_count": sum(
            item["status"] == "committed" for item in batch["sources"].values()
        ),
        "noop_count": sum(
            item["status"] == "noop" for item in batch["sources"].values()
        ),
        "observed_count": sum(
            item["status"] == "observed" for item in batch["sources"].values()
        ),
    }


def process_has_complete_routine_digest(
    kb: Path,
    *,
    run_id: str,
) -> bool:
    if not routine_inventory(kb):
        return True
    try:
        batch = _load_batch(kb, run_id)
    except DreamingError:
        return False
    return batch.get("status") == "completed"
