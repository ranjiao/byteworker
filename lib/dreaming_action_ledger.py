"""Fenced, idempotent ledger for Dreaming durable actions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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


LEDGER_SCHEMA = "byteworker-action-ledger/v1"
MAX_RECEIPT_BYTES = 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _action_path(kb: Path, action_id: str) -> Path:
    if not action_id or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in action_id
    ):
        raise DreamingError("DREAMING_ACTION_INVALID", "action_id 含非法字符。")
    return secure_path(kb, "actions", f"{action_id}.json")


def _read_action(kb: Path, action_id: str) -> dict[str, Any]:
    path = _action_path(kb, action_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_ACTION_NOT_FOUND",
            f"action 不存在或损坏: {action_id}",
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != LEDGER_SCHEMA:
        raise DreamingError("DREAMING_ACTION_INVALID", "action ledger schema 无效。")
    return value


def _write_action(kb: Path, value: Mapping[str, Any]) -> None:
    atomic_write_json(_action_path(kb, str(value["action_id"])), value)


def _lease_active(lease: object, now: datetime) -> bool:
    return (
        isinstance(lease, Mapping)
        and (parse_time(lease.get("expires_at")) or datetime.min.replace(tzinfo=timezone.utc))
        > now
    )


def _require_lease(
    state: Mapping[str, Any],
    *,
    token: str,
    now: datetime,
) -> Mapping[str, Any]:
    lease = state.get("active_lease")
    if (
        not _lease_active(lease, now)
        or not isinstance(lease, Mapping)
        or lease.get("token") != token
    ):
        raise DreamingError(
            "DREAMING_LEASE_MISMATCH",
            "action 操作要求当前有效 Dreaming lease。",
        )
    return lease


def plan_actions(
    kb: Path,
    *,
    plan: Mapping[str, Any],
    lease_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = _require_lease(state, token=lease_token, now=current)
        if plan.get("run_id") != lease_token:
            raise DreamingError(
                "DREAMING_ACTION_RUN_MISMATCH",
                "ActionPlan run_id 必须等于当前 lease token。",
            )
        results = []
        for action in plan["actions"]:
            if action["policy_result"] == "denied":
                results.append(
                    {
                        "action_id": action["action_id"],
                        "status": "denied",
                        "policy_reasons": action.get("policy_reasons", []),
                    }
                )
                continue
            action_id = action["action_id"]
            existing_index = state["actions"].get(action_id)
            if isinstance(existing_index, Mapping):
                existing = _read_action(kb, action_id)
                if (
                    existing.get("dedupe_key") != action["dedupe_key"]
                    or existing.get("plan_sha256") != plan["plan_sha256"]
                ):
                    raise DreamingError(
                        "DREAMING_ACTION_CONFLICT",
                        f"action_id 已绑定不同计划: {action_id}",
                    )
                results.append(
                    {"action_id": action_id, "status": existing["status"]}
                )
                continue
            duplicate = next(
                (
                    existing_id
                    for existing_id, summary in state["actions"].items()
                    if existing_id != action_id
                    and isinstance(summary, Mapping)
                    and summary.get("dedupe_key") == action["dedupe_key"]
                    and summary.get("status") != "cancelled"
                ),
                None,
            )
            if duplicate:
                raise DreamingError(
                    "DREAMING_ACTION_DEDUPE_CONFLICT",
                    f"dedupe_key 已绑定 action: {duplicate}",
                )
            status = (
                "awaiting_confirmation"
                if action["policy_result"] == "confirm"
                else "planned"
            )
            record = {
                "schema_version": LEDGER_SCHEMA,
                **dict(action),
                "run_id": lease_token,
                "job": lease["job"],
                "period": lease["period"],
                "lease_epoch": lease["epoch"],
                "grant_revision": plan["grant_revision"],
                "plan_sha256": plan["plan_sha256"],
                "status": status,
                "created_at": utc_iso(current),
                "updated_at": utc_iso(current),
                "claim": None,
                "downstream": None,
            }
            _write_action(kb, record)
            state["actions"][action_id] = {
                "status": status,
                "dedupe_key": action["dedupe_key"],
                "kind": action["kind"],
                "updated_at": utc_iso(current),
            }
            results.append({"action_id": action_id, "status": status})
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"run_id": lease_token, "actions": results}


def claim_action(
    kb: Path,
    *,
    action_id: str,
    lease_token: str,
    confirmed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = _require_lease(state, token=lease_token, now=current)
        action = _read_action(kb, action_id)
        if (
            action["run_id"] != lease_token
            or action["lease_epoch"] != lease["epoch"]
            or action["grant_revision"] != state["grants"]["revision"]
        ):
            raise DreamingError(
                "DREAMING_ACTION_RUN_MISMATCH",
                "action 不属于当前 lease epoch。",
            )
        if action["status"] == "awaiting_confirmation" and not confirmed:
            raise DreamingError(
                "DREAMING_ACTION_CONFIRMATION_REQUIRED",
                "该 action 必须先取得用户确认。",
            )
        if action["status"] == "claimed":
            return dict(action["claim"])
        if action["status"] not in {"planned", "awaiting_confirmation"}:
            raise DreamingError(
                "DREAMING_ACTION_STATE_INVALID",
                f"action 当前不可 claim: {action['status']}",
            )
        claim = {
            "schema_version": "byteworker-action-claim/v1",
            "action_id": action_id,
            "run_id": lease_token,
            "job": lease["job"],
            "period": lease["period"],
            "token": uuid.uuid4().hex,
            "lease_epoch": lease["epoch"],
            "status": "claimed",
            "claimed_at": utc_iso(current),
            "dedupe_key": action["dedupe_key"],
        }
        action["status"] = "claimed"
        action["claim"] = claim
        action["updated_at"] = utc_iso(current)
        _write_action(kb, action)
        state["actions"][action_id]["status"] = "claimed"
        state["actions"][action_id]["updated_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return claim


def validate_claim(
    kb: Path,
    *,
    action_id: str,
    claim_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        action = _read_action(kb, action_id)
        if action.get("grant_revision") != state["grants"]["revision"]:
            raise DreamingError(
                "DREAMING_GRANT_STALE",
                "action claim 后 grant revision 已变化。",
            )
        claim = action.get("claim")
        if (
            action.get("status") != "claimed"
            or not isinstance(claim, Mapping)
            or claim.get("token") != claim_token
        ):
            raise DreamingError(
                "DREAMING_ACTION_CLAIM_MISMATCH",
                "action claim 不存在或 token 不匹配。",
            )
        lease = _require_lease(state, token=str(claim["run_id"]), now=current)
        if lease.get("epoch") != claim.get("lease_epoch"):
            raise DreamingError(
                "DREAMING_ACTION_CLAIM_MISMATCH",
                "action claim lease epoch 已过期。",
            )
    return dict(claim)


def _verify_git_commit(kb: Path, commit: str, kind: str) -> None:
    if not commit:
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "持久化 action receipt 缺少 commit。",
        )
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(kb),
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "下游 receipt commit 不是 KB 中可验证的 commit。",
        )
    paths = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    allowed = {
        "include_report": lambda path: path.startswith(("reports/", "journal/")),
        "todo_candidate": lambda path: path == "todo.md" or path.startswith("journal/"),
        "knowledge_candidate": lambda path: path == "INDEX.md"
        or path.startswith(("raw_data/", "provenance/", "knowledge/", "journal/")),
    }
    predicate = allowed[kind]
    if not paths or any(not predicate(path) for path in paths):
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "下游 commit 变更路径与 action kind 不匹配。",
            details={"paths": sorted(paths)},
        )


def _receipt_summary(
    kb: Path,
    receipt: Mapping[str, Any],
    dedupe_key: str,
    kind: str,
) -> dict[str, Any]:
    if receipt.get("status") not in {"committed", "noop"}:
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "下游 receipt status 必须是 committed 或 noop。",
        )
    if receipt.get("idempotency_key") != dedupe_key:
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "下游 receipt idempotency_key 与 action 不一致。",
        )
    durable_kinds = {"include_report", "todo_candidate", "knowledge_candidate"}
    if kind in durable_kinds:
        if receipt["status"] != "committed":
            raise DreamingError(
                "DREAMING_ACTION_RECEIPT_INVALID",
                "持久化 action 必须提供 committed Git receipt。",
            )
        _verify_git_commit(kb, str(receipt.get("commit", "")), kind)
    elif kind == "instant_alert":
        if not str(receipt.get("delivery_id", "")).strip():
            raise DreamingError(
                "DREAMING_ACTION_RECEIPT_INVALID",
                "instant_alert receipt 缺少 delivery_id。",
            )
    elif receipt["status"] != "noop":
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "无持久写入 action 只接受 noop receipt。",
        )
    payload = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "status": receipt["status"],
        "idempotency_key": dedupe_key,
        "commit": str(receipt.get("commit", "")),
        "receipt_sha256": hashlib.sha256(payload).hexdigest(),
    }


def load_downstream_receipt(path: Path, *, skill_root: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    root = skill_root.resolve()
    if resolved == root or root in resolved.parents:
        raise DreamingError(
            "DREAMING_OUTPUT_IN_SKILL_REPO",
            "下游 receipt 不得位于 byteworker skill 仓库。",
        )
    try:
        if resolved.stat().st_size > MAX_RECEIPT_BYTES:
            raise DreamingError(
                "DREAMING_ACTION_RECEIPT_INVALID",
                "下游 receipt 超过 1 MiB 上限。",
            )
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except DreamingError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "无法读取下游 receipt JSON。",
        ) from exc
    if not isinstance(value, dict):
        raise DreamingError(
            "DREAMING_ACTION_RECEIPT_INVALID",
            "下游 receipt 必须是 object。",
        )
    return value


def complete_action(
    kb: Path,
    *,
    action_id: str,
    claim_token: str,
    receipt: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        action = _read_action(kb, action_id)
        if action["status"] == "committed":
            downstream = _receipt_summary(
                kb,
                receipt,
                action["dedupe_key"],
                action["kind"],
            )
            if downstream["receipt_sha256"] != (action.get("downstream") or {}).get(
                "receipt_sha256"
            ):
                raise DreamingError(
                    "DREAMING_ACTION_RECEIPT_INVALID",
                    "committed action 收到不同下游 receipt。",
                )
            return {
                "action_id": action_id,
                "status": "committed",
                "downstream": downstream,
            }
        if (
            action["status"] != "reconcile"
            and action.get("grant_revision") != state["grants"]["revision"]
        ):
            raise DreamingError(
                "DREAMING_GRANT_STALE",
                "action claim 后 grant revision 已变化。",
            )
        claim = action.get("claim")
        if (
            action["status"] not in {"claimed", "reconcile"}
            or not isinstance(claim, Mapping)
            or claim.get("token") != claim_token
        ):
            raise DreamingError(
                "DREAMING_ACTION_CLAIM_MISMATCH",
                "action claim token 不匹配。",
            )
        downstream = _receipt_summary(
            kb,
            receipt,
            action["dedupe_key"],
            action["kind"],
        )
        action["status"] = "committed"
        action["downstream"] = downstream
        action["updated_at"] = utc_iso(current)
        _write_action(kb, action)
        state["actions"][action_id]["status"] = "committed"
        state["actions"][action_id]["updated_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {
        "action_id": action_id,
        "status": "committed",
        "downstream": downstream,
    }


def cancel_action(
    kb: Path,
    *,
    action_id: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        action = _read_action(kb, action_id)
        if action["status"] not in {"planned", "awaiting_confirmation"}:
            raise DreamingError(
                "DREAMING_ACTION_STATE_INVALID",
                "已 claim/committed 的 action 不能直接取消。",
            )
        action["status"] = "cancelled"
        action["cancel_reason"] = reason.strip() or "cancelled"
        action["updated_at"] = utc_iso(current)
        _write_action(kb, action)
        state["actions"][action_id]["status"] = "cancelled"
        state["actions"][action_id]["updated_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
    return {"action_id": action_id, "status": "cancelled"}


def reconcile_action(
    kb: Path,
    *,
    action_id: str,
    receipt: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        action = _read_action(kb, action_id)
        if action["status"] == "committed":
            return {"action_id": action_id, "status": "committed"}
        if action["status"] not in {"claimed", "reconcile"}:
            raise DreamingError(
                "DREAMING_ACTION_STATE_INVALID",
                "只有 claimed/reconcile action 可以对账。",
            )
        if receipt is None:
            action["status"] = "reconcile"
            action["updated_at"] = utc_iso(current)
            _write_action(kb, action)
            state["actions"][action_id]["status"] = "reconcile"
            state["updated_at"] = utc_iso(current)
            save_state_unlocked(kb, state)
            return {"action_id": action_id, "status": "reconcile"}
        claim = action.get("claim")
        claim_token = str(claim.get("token", "")) if isinstance(claim, Mapping) else ""
    return complete_action(
        kb,
        action_id=action_id,
        claim_token=claim_token,
        receipt=receipt,
        now=current,
    )


def invalidate_actions_for_grant_change_unlocked(
    kb: Path,
    *,
    state: dict[str, Any],
    now: datetime,
) -> dict[str, int]:
    cancelled = 0
    reconcile = 0
    for action_id, summary in list(state["actions"].items()):
        if not isinstance(summary, dict):
            continue
        action = _read_action(kb, action_id)
        if action["status"] in {"planned", "awaiting_confirmation"}:
            action["status"] = "cancelled"
            action["cancel_reason"] = "DREAMING_GRANT_REVOKED"
            cancelled += 1
        elif action["status"] == "claimed":
            action["status"] = "reconcile"
            reconcile += 1
        else:
            continue
        action["updated_at"] = utc_iso(now)
        _write_action(kb, action)
        summary["status"] = action["status"]
        summary["updated_at"] = utc_iso(now)
    return {"cancelled": cancelled, "reconcile": reconcile}
