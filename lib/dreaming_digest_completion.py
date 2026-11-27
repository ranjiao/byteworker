"""Verify source receipts and settle routine digest batches before refreshing reports."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from digest_run_log import SHARD_FAILURE_CODES
from dreaming_digest import (
    BATCH_SCHEMA,
    EXECUTION_REQUIRED,
    advance_source_checkpoint,
    live_process_lease,
    load_routine_batch,
    routine_batch_path,
    routine_inventory,
    source_covers_window,
)
from dreaming_digest_receipts import (
    BUDGET_DEFERRED,
    SUCCESS_STATUSES,
    failure_evidence,
    prepared_flow,
    recover_linked_receipt,
    verify_source_success,
)
from dreaming_reports import refresh_report_dependencies
from dreaming_run_log import MACHINE_CODE, append_run_event
from dreaming_state import (
    DreamingError,
    atomic_write_json,
    load_state_unlocked,
    parse_time,
    save_state_unlocked,
    state_lock,
    utc_iso,
)
from frontmatter import parse_file, parse_frontmatter
from source_profile_contract import SourceProfileError
from source_profiles import load_profile, profile_revision


RESULT_SCHEMA = "byteworker-dreaming-digest-result/v1"
SKILL_ROOT = Path(__file__).resolve().parents[1]


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


def _commit_changes(
    kb: Path, commit: str, raw_receipt: Mapping[str, Any] | None = None,
) -> tuple[dict[str, int], list[dict[str, str]]] | None:
    if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit):
        return None
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--root", "--no-renames", "--no-commit-id", "-r", "-z",
             "--name-status", commit, "--"],
            cwd=kb, capture_output=True, check=True,
        )
        fields = result.stdout.decode("utf-8", errors="replace").split("\0")
        changes = list(zip(fields[::2], fields[1::2]))
        if raw_receipt is not None:
            for _, path in changes:
                if not path.startswith("raw_data/") or not path.endswith(".md"):
                    continue
                raw = kb / path
                if raw.resolve().parent != (kb / "raw_data").resolve() or not raw.is_file():
                    continue
                frontmatter, _ = parse_file(str(raw))
                if (frontmatter.get("raw_id") == raw_receipt.get("raw_id")
                        and frontmatter.get("digest_key") == raw_receipt.get("digest_key")):
                    break
            else:
                return None
    except (OSError, UnicodeError, subprocess.CalledProcessError):
        return None
    nodes = []
    actions = {"A": "新增", "M": "更新", "D": "删除"}
    for status, path in changes:
        if status not in actions or not path.startswith("knowledge/") or not path.endswith(".md"):
            continue
        revision = f"{commit}^" if status == "D" else commit
        blob = subprocess.run(["git", "cat-file", "blob", f"{revision}:{path}"], cwd=kb, capture_output=True)
        title = ""
        if blob.returncode == 0:
            meta, _ = parse_frontmatter(blob.stdout.decode("utf-8", errors="replace"))
            title = str(meta.get("title") or "")
        nodes.append({"action": actions[status], "path": path, "title": title or Path(path).stem})
    return {
        "digest_commits": int(raw_receipt is not None),
        "files_changed": len(changes),
        "nodes_created": sum(node["action"] == "新增" for node in nodes),
        "nodes_updated": sum(node["action"] == "更新" for node in nodes),
        "nodes_deleted": sum(node["action"] == "删除" for node in nodes),
    }, nodes


def routine_run_accounting(kb: Path, run_id: str, *, action_commits: list[str] | None = None) -> dict[str, Any]:
    """Summarize recorded receipts without claiming writes from unfinished sources."""
    try:
        batch = load_routine_batch(kb, run_id)
    except DreamingError:
        if not action_commits:
            return {"routine_batch_recorded": False}
        batch = None
    totals = dict.fromkeys(("digest_commits", "files_changed", "nodes_created", "nodes_updated", "nodes_deleted"), 0)
    seen_commits = set()
    changed_nodes = {}
    sources = []
    unaccounted = 0
    for key, source in (batch["sources"] if batch else {}).items():
        if not isinstance(source, Mapping):
            unaccounted += 1
            continue
        status = source.get("status", "unknown")
        receipt = source.get("receipt")
        changes = dict.fromkeys(totals, 0) if status in {"noop", "empty_window", "covered"} else None
        if status == "committed" and isinstance(receipt, Mapping):
            verified = _commit_changes(kb, str(receipt.get("commit", "")), receipt)
            if verified is not None:
                changes, nodes = verified
                for node in nodes:
                    changed_nodes[(node["action"], node["path"])] = node
        if changes is None:
            unaccounted += 1
        elif status == "committed" and receipt["commit"] not in seen_commits:
            seen_commits.add(receipt["commit"])
            for name, count in changes.items():
                totals[name] += count
        sources.append({
            "source_key": key, "source_type": source.get("source_type", ""),
            "label": source.get("label", ""), "status": status, "kb_operations": changes,
            **({"error_code": source["error_code"]} if source.get("error_code") else {}),
            **({"failure_evidence": receipt["failure_evidence"]}
               if isinstance(receipt, Mapping) and receipt.get("failure_evidence") else {}),
        })
    unknown_action_changes = 0
    for commit in sorted(set(action_commits or []) - seen_commits):
        verified = _commit_changes(kb, commit)
        if verified is None:
            unknown_action_changes += 1
            continue
        changes, nodes = verified
        for name, count in changes.items():
            totals[name] += count
        for node in nodes:
            changed_nodes[(node["action"], node["path"])] = node
    return {
        "routine_batch_recorded": batch is not None,
        "unknown_action_changes": unknown_action_changes,
        "routine_sources": sources,
        "changed_nodes": list(changed_nodes.values()),
        "unaccounted_sources": unaccounted,
        "kb_operations": totals,
    }


def record_routine_digest_result(
    kb: Path,
    *,
    token: str,
    input_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    return record_routine_digest(kb, token=token, result=_load_result(input_path), now=now)


def _reject_early_completion(
    kb: Path, lease: Mapping[str, Any], digest_run_id: str, message: str, *, now: datetime,
    log_settings: Mapping[str, int],
) -> None:
    append_run_event(
        kb, run_id=lease["run_id"], event="source_completion_rejected", job="process",
        period=lease["period"], owner=lease["owner"], epoch=lease["epoch"],
        stage="analysis", status="running", error_code=EXECUTION_REQUIRED,
        digest_run_id=digest_run_id, now=now, **log_settings,
    )
    raise DreamingError(EXECUTION_REQUIRED, message, details={"digest_run_id": digest_run_id} if digest_run_id else None)


def record_routine_digest(
    kb: Path, *, token: str, result: Mapping[str, Any], now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    source_key = str(result.get("source_key", ""))
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = live_process_lease(state, token=token, now=current)
        batch = load_routine_batch(kb, str(lease["run_id"]))
        source = batch["sources"].get(source_key)
        if not isinstance(source, dict) or batch["status"] != "running":
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
        status = result.get("status")
        error_code = ""
        if status in ("failed", "blocked"):
            error_code = str(result.get("error_code", ""))
            if not MACHINE_CODE.fullmatch(error_code):
                raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "来源失败必须提供固定错误码。")
            verified = {"status": status}
            flow = prepared_flow(kb, source)
            if flow is not None or error_code == BUDGET_DEFERRED or error_code in SHARD_FAILURE_CODES:
                evidence = failure_evidence(kb, flow, error_code) if flow is not None else None
                if evidence is None:
                    _reject_early_completion(
                        kb, lease, flow.name if flow is not None else "",
                        "缺少与当前来源对应的失败证据。继续处理缺失分片，或用 digest-parallel attempt / "
                        "digest-flow fail 记录实际阻塞后再登记。",
                        now=current, log_settings=state["logging"],
                    )
                verified["failure_evidence"] = evidence
        else:
            verified = verify_source_success(kb, source, result, batch, lease, current)
        if source["status"] in SUCCESS_STATUSES and source["receipt"] != verified:
            raise DreamingError(
                "DREAMING_DIGEST_RESULT_CONFLICT",
                "同一来源已记录不同 digest receipt。",
            )
        source["status"] = verified["status"]
        source["receipt"] = verified
        source["error_code"] = error_code
        source["finished_at"] = utc_iso(current)
        atomic_write_json(routine_batch_path(kb, str(lease["run_id"])), batch)
        if verified["status"] in SUCCESS_STATUSES:
            if source["origin"] == "profile":
                try:
                    profile = load_profile(kb, source["source_uid"])
                    unchanged = profile["routine"]["enabled"] and profile_revision(profile) == source["profile_revision"]
                except SourceProfileError:
                    unchanged = False
            else:
                unchanged = any(item["source_key"] == source_key and item["profile_revision"] == source["profile_revision"]
                                for item in routine_inventory(kb))
            if unchanged:
                advance_source_checkpoint(state, source, batch, current)
                state["updated_at"] = utc_iso(current)
                save_state_unlocked(kb, state)

    return {
        "run_id": lease["run_id"],
        "source_key": source_key,
        "status": verified["status"],
        **({"failure_evidence": verified["failure_evidence"]} if "failure_evidence" in verified else {}),
    }


def complete_routine_digest(
    kb: Path,
    *,
    token: str,
    allow_partial: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        lease = live_process_lease(state, token=token, now=current)
        run_id = str(lease["run_id"])
        batch = load_routine_batch(kb, run_id)
        start = parse_time(batch.get("target_start") or batch["target_through"])
        end = parse_time(batch["target_through"])
        for source in batch["sources"].values():
            recover_linked_receipt(kb, source, batch, lease, current)
            evidence = source.get("receipt", {}).get("failure_evidence", {})
            if source["status"] not in SUCCESS_STATUSES and not evidence:
                flow = prepared_flow(kb, source)
                if flow is not None:
                    _reject_early_completion(
                        kb, lease, flow.name, "仍有已准备分析的来源缺少完成或失败回执，请先执行并登记来源结果。",
                        now=current, log_settings=state["logging"],
                    )
        checkpoints = state.setdefault("source_checkpoints", {})
        for source in batch["sources"].values():
            if source.get("status") == "covered" and not source_covers_window(
                checkpoints.get(source["source_key"]), source, start, end,
            ):
                source["status"] = "pending"
        pending = [
            item["source_key"]
            for item in batch["sources"].values()
            if item.get("status") not in SUCCESS_STATUSES
        ]
        unrecorded = [key for key in pending if batch["sources"][key]["status"] == "pending"]
        if unrecorded:
            raise DreamingError(
                EXECUTION_REQUIRED, "仍有来源缺少完成或失败回执，批次保持打开。",
                details={"pending_source_keys": unrecorded},
            )
        if pending and not allow_partial:
            raise DreamingError(
                "DREAMING_DIGEST_INCOMPLETE",
                "仍有定期来源未取得已核验的完成回执。",
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
        for source in batch["sources"].values():
            advance_source_checkpoint(state, source, batch, current)
        batch["status"] = "partial" if pending else "completed"
        batch["completed_at"] = utc_iso(current)
        state["updated_at"] = utc_iso(current)
        save_state_unlocked(kb, state)
        atomic_write_json(routine_batch_path(kb, run_id), batch)
    refresh_report_dependencies(kb, now=current)
    return {
        "schema_version": BATCH_SCHEMA,
        "run_id": run_id,
        "status": batch["status"],
        "target_through": batch["target_through"],
        "source_count": len(batch["sources"]),
        "incomplete_count": len(pending),
        "incomplete_sources": [
            {"source_key": key, "status": batch["sources"][key]["status"],
             "error_code": batch["sources"][key].get("error_code", "")}
            for key in pending
        ],
        "committed_count": sum(
            item["status"] == "committed" for item in batch["sources"].values()
        ),
        "noop_count": sum(
            item["status"] == "noop" for item in batch["sources"].values()
        ),
        "observed_count": sum(
            item["status"] == "observed" for item in batch["sources"].values()
        ),
        "empty_window_count": sum(
            item["status"] == "empty_window" for item in batch["sources"].values()
        ),
        "covered_count": sum(item["status"] == "covered" for item in batch["sources"].values()),
    }
