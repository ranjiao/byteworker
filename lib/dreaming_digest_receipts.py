"""Verify downstream success and failure evidence for routine sources."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from frontmatter import parse_file
from digest_run_log import SHARD_ATTEMPT_EVENTS, SHARD_CONTEXT_RETRY_EXHAUSTED, SHARD_FAILURE_CODES, diagnostic_events
from operation_trace import DIGEST_RUN_ID
from sources import SourceBundleError, load_source_bundle
from sources.adapters.text import chat_window, empty_chat_window
from dreaming_state import DreamingError, parse_time, utc_iso


SUCCESS_STATUSES = {"committed", "noop", "observed", "empty_window", "covered"}
TRANSACTION_SUCCESS_STATUSES = {"committed", "noop"}
BUDGET_DEFERRED = "DIGEST_BUDGET_DEFERRED"


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
    *,
    target_through: str,
    acquired_at: str,
    now: datetime,
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
    window = None
    if source["source_type"] == "feishu_chat":
        window = chat_window(frontmatter.get("source_window"))
        _verify_chat_coverage(window, target_through, acquired_at, now, source)
    return {
        **({"window_start": utc_iso(window[0]), "window_end": utc_iso(window[1])} if window else {}),
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


def _verify_chat_coverage(window, target_through: str, acquired_at: str, now: datetime, source) -> None:
    if source.get("capture_window"):
        needed = chat_window(source["capture_window"])
        if window and needed and window[0] <= needed[0] and needed[1] <= window[1] <= now:
            return
        raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "群聊窗口未覆盖租约范围，或不是本轮抓取。")
    target = parse_time(target_through)
    acquired = parse_time(acquired_at)
    if (
        window is None or target is None or acquired is None
        or not window[0] <= target <= window[1]
        or not acquired.replace(microsecond=0) <= window[1] <= now
    ):
        raise DreamingError("DREAMING_DIGEST_RESULT_INVALID", "群聊窗口未覆盖本轮目标时间，或不是本轮抓取。")


def _verify_empty_chat_receipt(
    kb: Path,
    source: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    target_through: str,
    acquired_at: str,
    now: datetime,
) -> dict[str, Any]:
    invalid = "DREAMING_DIGEST_RESULT_INVALID"
    receipt = result.get("receipt")
    if not isinstance(receipt, Mapping) or receipt.get("status") != "empty_window":
        raise DreamingError(invalid, "缺少群聊空窗口回执。")
    run_id = receipt.get("digest_run_id")
    if not isinstance(run_id, str) or not DIGEST_RUN_ID.fullmatch(run_id):
        raise DreamingError(invalid, "群聊空窗口回执缺少有效摄取编号。")
    bundle_path = kb.resolve() / "state" / "digest" / "flows" / run_id / "source-bundle.json"
    if bundle_path.resolve() != bundle_path:
        raise DreamingError(invalid, "群聊空窗口的 Bundle 路径无效。")
    try:
        bundle = load_source_bundle(bundle_path)
        window = empty_chat_window(bundle)
    except (OSError, SourceBundleError) as exc:
        raise DreamingError(invalid, "无法核验群聊空窗口 Bundle。") from exc
    metadata = bundle.provider_metadata
    profile = metadata.get("source_profile")
    if (
        window is None
        or source["source_type"] != "feishu_chat"
        or bundle.identity.source_uid != source["source_uid"]
        or metadata.get("capture_mode") not in ({"since-last", "explicit"} if source.get("capture_window") else {"since-last"})
        or not isinstance(profile, Mapping)
        or profile.get("revision") != source["profile_revision"]
    ):
        raise DreamingError(invalid, "群聊空窗口与本轮来源或配置版本不符。")
    start, end = window
    _verify_chat_coverage(window, target_through, acquired_at, now, source)
    return {
        "status": "empty_window",
        "digest_run_id": run_id,
        "window_start": utc_iso(start),
        "window_end": utc_iso(end),
    }


def _linked_flow(kb: Path, source: Mapping[str, Any]) -> tuple[Path, dict[str, Any]] | None:
    run_id = str(source.get("work", {}).get("digest_run_id", ""))
    if not DIGEST_RUN_ID.fullmatch(run_id):
        return None
    path = kb.resolve() / "state" / "digest" / "flows" / run_id / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.resolve() == path else None
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("run_id") != run_id:
        return None
    return path.parent, state


def prepared_flow(kb: Path, source: Mapping[str, Any]) -> Path | None:
    linked = _linked_flow(kb, source)
    if linked is None:
        return None
    path, state = linked
    if state.get("phase") in {"prepared", "commit_failed"} or (
        state.get("phase") == "failed" and state.get("resume_phase") in {"prepared", "commit_failed"}
    ):
        return path
    return None


def verify_source_success(kb: Path, source: Mapping[str, Any], result: Mapping[str, Any],
                           batch: Mapping[str, Any], lease: Mapping[str, Any],
                           now: datetime) -> dict[str, Any]:
    if source["workflow"] == "wiki_scan_then_digest":
        return _verify_wiki_receipt(kb, source, result)
    verify = _verify_empty_chat_receipt if result.get("status") == "empty_window" else _verify_transaction_receipt
    return verify(kb, source, result, target_through=batch["target_through"],
                  acquired_at=lease.get("target_acquired_at", lease["acquired_at"]), now=now)


def recover_linked_receipt(kb: Path, source: dict[str, Any], batch: Mapping[str, Any],
                            lease: Mapping[str, Any], now: datetime) -> None:
    if source["workflow"] != "ordinary_digest" or source["status"] in SUCCESS_STATUSES:
        return
    linked = _linked_flow(kb, source)
    if linked is None:
        return
    _, flow = linked
    receipt = flow.get("receipt")
    if not isinstance(receipt, Mapping):
        return
    status = receipt.get("status")
    phase = "noop" if status == "empty_window" else status
    if status not in {"committed", "noop", "empty_window"} or phase != flow.get("phase"):
        return
    verified = verify_source_success(kb, source, {"status": status, "receipt": receipt}, batch, lease, now)
    source.update(status=status, receipt=verified, error_code="", finished_at=utc_iso(now))


def failure_evidence(kb: Path, flow: Path, error_code: str) -> dict[str, Any] | None:
    events, _ = diagnostic_events(kb, run_id=flow.name)
    lifecycle = [event for event in events if event.get("event") != "usage_recorded"]
    if lifecycle and lifecycle[-1].get("event") == "failed" and lifecycle[-1].get("detail_code") == error_code:
        return {"digest_run_id": flow.name, "kind": "flow_reported_failure"}
    if error_code == BUDGET_DEFERRED or error_code in SHARD_FAILURE_CODES:
        latest = {
            (event["stage"], event["shard_id"]): event
            for event in events if event.get("event") in SHARD_ATTEMPT_EVENTS
        }
        exhausted = {
            (event["plan_hash"], event["stage"], event["shard_id"], event["attempt_id"]): event["detail_code"]
            for event in events if event.get("event") == SHARD_CONTEXT_RETRY_EXHAUSTED
        }
        for (stage, shard_id), event in latest.items():
            if stage not in {"dependency", "semantic", "conflict"} or not re.fullmatch(
                rf"{stage}-\d{{3}}(?:-[0-9a-f]{{12}})?", shard_id,
            ):
                continue
            host_error = event.get("host_error")
            if error_code == BUDGET_DEFERRED:
                if event.get("status") != "over_budget" or not (
                    set(event["budget"]["exceeded_limits"]) - {"host_input_limit"}
                ):
                    continue
            elif (
                event.get("event") != "shard_execution_failed" or event.get("detail_code") != error_code
            ):
                continue
            plan_path = flow / stage / f"{stage}-plan.json"
            try:
                plan_bytes = plan_path.read_bytes()
                plan = json.loads(plan_bytes)
            except (OSError, ValueError):
                continue
            if (
                not isinstance(plan, dict) or event.get("plan_hash") != hashlib.sha256(plan_bytes).hexdigest()
                or (plan_path.parent / "results" / f"{shard_id}.json").is_file()
            ):
                continue
            retry_reason = None
            if error_code == "DIGEST_HOST_CONTEXT_LIMIT" and host_error:
                retry_reason = exhausted.get((event["plan_hash"], stage, shard_id, event["attempt_id"]))
            return {
                "digest_run_id": flow.name, "stage": stage, "shard_id": shard_id,
                "kind": "budget_check" if error_code == BUDGET_DEFERRED else (
                    "host_verified_failure" if host_error else "host_reported_failure"
                ),
                "budget": event["budget"],
                "attempt_id": event["attempt_id"],
                **({"host_error": host_error} if host_error else {}),
                **({"context_retry_exhausted": retry_reason} if retry_reason else {}),
            }
        return None
    latest_stages = {
        event["stage"]: event for event in events
        if event.get("event") in {"stage_started", "stage_completed", "stage_failed"}
    }
    for stage in ("dependency_review", "semantic_analysis", "reduce", "conflict_review",
                  "candidate_generation", "transaction_validate", "transaction", "finalize"):
        event = latest_stages.get(stage, {})
        if event.get("event") == "stage_failed" and event.get("detail_code") == error_code:
            return {"digest_run_id": flow.name, "stage": stage, "kind": "stage_failure"}
    return None
