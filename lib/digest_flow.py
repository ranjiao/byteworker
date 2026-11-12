"""Deterministic orchestration for non-semantic digest lifecycle work."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from digest_analysis import prepare_analysis_packet
from digest_capture import DigestCaptureError, execute_capture_plan
from digest_parallel import plan_parallel_work
from digest_run_log import (
    MACHINE_CODE_RE,
    finish_run,
    record_stage,
    show_run,
    start_run,
)
from digest_txn import execute_plan, preflight_bundle
from source_bundle_request import build_bundle_from_request
from source_capture import write_capture
from sources import load_source_bundle
from sources.adapters.text import empty_chat_window


FLOW_SCHEMA = "byteworker-digest-flow/v1"
DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]
FLOW_PHASES = {
    "classified",
    "captured",
    "capture_failed",
    "checked",
    "prepared",
    "prepare_failed",
    "commit_failed",
    "committed",
    "noop",
}


class DigestFlowError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.details:
            value["details"] = self.details
        return value


def _flows_root(kb: Path) -> Path:
    return kb.expanduser().resolve() / "state" / "digest" / "flows"


def _flow_dir(kb: Path, run_id: str) -> Path:
    return _flows_root(kb) / run_id


def _state_path(kb: Path, run_id: str) -> Path:
    return _flow_dir(kb, run_id) / "state.json"


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise DigestFlowError("DIGEST_FLOW_PATH_INVALID", "flow 目录不能是符号链接。")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


@contextmanager
def _flow_lock(kb: Path) -> Iterator[None]:
    root = _flows_root(kb)
    _ensure_private_directory(root)
    lock_path = root / ".lock"
    if lock_path.is_symlink():
        raise DigestFlowError("DIGEST_FLOW_PATH_INVALID", "flow lock 不能是符号链接。")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)
    with os.fdopen(descriptor, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_state(path: Path, value: Mapping[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".flow-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load_state(kb: Path, run_id: str) -> dict[str, Any]:
    path = _state_path(kb, run_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DigestFlowError(
            "DIGEST_FLOW_NOT_FOUND", f"digest flow 不存在或损坏: {run_id}"
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != FLOW_SCHEMA
        or value.get("run_id") != run_id
        or value.get("phase") not in FLOW_PHASES
    ):
        raise DigestFlowError("DIGEST_FLOW_STATE_INVALID", "digest flow state 非法。")
    return value


def _inside_flow(kb: Path, run_id: str, path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    root = _flow_dir(kb, run_id).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DigestFlowError(
            "DIGEST_FLOW_PATH_INVALID",
            "flow request、plan 和中间产物必须位于该 run 的私密 work_dir。",
        ) from exc
    return resolved


def _record_failure(kb: Path, run_id: str, stage: str, code: str) -> None:
    try:
        record_stage(
            kb,
            run_id=run_id,
            stage=stage,
            status="failed",
            detail_code=code,
        )
    except Exception:
        pass


def start_flow(kb: Path, *, source_type: str, source_ref: str = "") -> dict[str, Any]:
    started = start_run(kb, source_type=source_type, source_ref=source_ref)
    run_id = str(started["run_id"])
    try:
        record_stage(kb, run_id=run_id, stage="classify", status="started")
        record_stage(
            kb,
            run_id=run_id,
            stage="classify",
            status="completed",
            source_type=source_type,
            detail_code="DIGEST_FLOW_CLASSIFIED",
        )
        state = {
            "schema_version": FLOW_SCHEMA,
            "run_id": run_id,
            "source_type": source_type,
            "source_ref_hash": started.get("source_ref_hash", ""),
            "phase": "classified",
            "capture_count": 0,
            "artifacts": {},
        }
        with _flow_lock(kb):
            _atomic_state(_state_path(kb, run_id), state)
    except Exception:
        try:
            finish_run(
                kb,
                run_id=run_id,
                status="failed",
                error_code="DIGEST_FLOW_START_FAILED",
            )
        except Exception:
            pass
        raise
    return {
        "schema_version": FLOW_SCHEMA,
        "run_id": run_id,
        "phase": "classified",
        "work_dir": str(_flow_dir(kb, run_id)),
        "next_action": "capture_or_prepare",
    }


def capture_flow(
    kb: Path,
    *,
    run_id: str,
    request_path: Path,
    skill_root: Path = DEFAULT_SKILL_ROOT,
    runner: Callable[..., Any] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    request = _inside_flow(kb, run_id, request_path)
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
    if state["phase"] in {"committed", "noop"}:
        raise DigestFlowError("DIGEST_FLOW_TERMINAL", "digest flow 已终态。")
    record_stage(
        kb,
        run_id=run_id,
        stage="capture",
        status="started",
        detail_code="DIGEST_FLOW_CAPTURE_STARTED",
    )
    try:
        kwargs: dict[str, Any] = {"skill_root": skill_root}
        if runner is not None:
            kwargs["runner"] = runner
        if sleeper is not None:
            kwargs["sleeper"] = sleeper
        receipt = execute_capture_plan(request, **kwargs)
        retries = sum(max(0, int(item.get("attempts", 0)) - 1) for item in receipt["jobs"])
        record_stage(
            kb,
            run_id=run_id,
            stage="capture",
            status="completed",
            detail_code="DIGEST_FLOW_CAPTURE_COMPLETED",
            metrics={
                "component_count": int(receipt["job_count"]),
                "input_bytes": int(receipt["output_bytes"]),
                "retry_count": retries,
                "worker_count": min(int(receipt["max_workers"]), int(receipt["job_count"])),
            },
        )
    except Exception as exc:
        code = (
            exc.code if isinstance(exc, DigestCaptureError) else "DIGEST_FLOW_CAPTURE_FAILED"
        )
        _record_failure(kb, run_id, "capture", code)
        with _flow_lock(kb):
            state = _load_state(kb, run_id)
            state["phase"] = "capture_failed"
            _atomic_state(_state_path(kb, run_id), state)
        code = str(getattr(exc, "code", "DIGEST_FLOW_CAPTURE_FAILED"))
        raise DigestFlowError(code, str(exc)) from exc
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
        state["phase"] = "captured"
        state["capture_count"] = int(state.get("capture_count", 0)) + 1
        _atomic_state(_state_path(kb, run_id), state)
    return {
        "schema_version": FLOW_SCHEMA,
        "run_id": run_id,
        "phase": "captured",
        "capture_count": state["capture_count"],
        "receipt": receipt,
        "next_action": "capture_or_prepare",
    }


def prepare_flow(
    kb: Path,
    *,
    run_id: str,
    bundle_request: Path | None = None,
    bundle_input: Path | None = None,
    check_only: bool = False,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    if (bundle_request is None) == (bundle_input is None):
        raise DigestFlowError("DIGEST_FLOW_INPUT_INVALID", "必须提供一个 bundle 或 bundle request。")
    request = _inside_flow(kb, run_id, bundle_request or bundle_input)
    work_dir = _flow_dir(kb, run_id)
    bundle_path = work_dir / "source-bundle.json"
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
    if state["phase"] in {"committed", "noop"}:
        raise DigestFlowError("DIGEST_FLOW_TERMINAL", "digest flow 已终态。")
    try:
        record_stage(
            kb,
            run_id=run_id,
            stage="bundle",
            status="started",
            detail_code="DIGEST_FLOW_BUNDLE_STARTED",
        )
        bundle = (
            load_source_bundle(request) if bundle_input is not None
            else build_bundle_from_request(str(state["source_type"]), request, skill_root=skill_root)
        )
        if bundle.identity.source_type != state["source_type"]:
            raise DigestFlowError("DIGEST_FLOW_INPUT_INVALID", "Bundle 来源类型与 run 不一致。")
        write_capture(bundle_path, bundle.to_dict(), skill_root=skill_root)
        record_stage(
            kb,
            run_id=run_id,
            stage="bundle",
            status="completed",
            detail_code="DIGEST_FLOW_BUNDLE_COMPLETED",
            metrics={"component_count": len(bundle.components)},
        )

        if empty_chat_window(bundle) is not None:
            receipt = {"status": "empty_window", "digest_run_id": run_id}
            finish_run(kb, run_id=run_id, status="noop")
            with _flow_lock(kb):
                state = _load_state(kb, run_id)
                state["phase"] = "noop"
                state["artifacts"] = {"bundle": str(bundle_path)}
                state["receipt"] = receipt
                state["next_action"] = "none"
                _atomic_state(_state_path(kb, run_id), state)
            return {
                "schema_version": FLOW_SCHEMA,
                "run_id": run_id,
                "phase": "noop",
                "receipt": receipt,
                "next_action": "none",
            }

        record_stage(kb, run_id=run_id, stage="preflight", status="started")
        flight = preflight_bundle(kb, bundle_path)
        record_stage(
            kb,
            run_id=run_id,
            stage="preflight",
            status="completed",
            detail_code="DIGEST_FLOW_PREFLIGHT_COMPLETED",
            metrics={"component_count": len(bundle.components)},
        )
        if flight["state"] == "noop":
            receipt = {
                "status": "noop", "digest_key": flight["digest_key"],
                "raw_path": flight["existing"]["path"], "digest_run_id": run_id,
            }
            finish_run(kb, run_id=run_id, status="noop")
            with _flow_lock(kb):
                state = _load_state(kb, run_id)
                state["phase"] = "noop"
                state["artifacts"] = {"bundle": str(bundle_path)}
                state["receipt"] = receipt
                state["preflight_state"] = "noop"
                state["next_action"] = "none"
                _atomic_state(_state_path(kb, run_id), state)
            return {
                "schema_version": FLOW_SCHEMA,
                "run_id": run_id,
                "phase": "noop",
                "preflight_state": "noop",
                "receipt": receipt,
                "next_action": "none",
            }

        with _flow_lock(kb):
            state = _load_state(kb, run_id)
            state.update(
                phase="checked", artifacts={"bundle": str(bundle_path)},
                preflight_state=flight["state"], next_action="analysis_prepare",
            )
            _atomic_state(_state_path(kb, run_id), state)
    except Exception as exc:
        _prepare_failure(kb, run_id, exc, ("preflight", "bundle"))

    if check_only:
        return {
            "schema_version": FLOW_SCHEMA, "run_id": run_id, "phase": "checked",
            "preflight_state": flight["state"], "next_action": "analysis_prepare",
        }
    return analyze_flow(kb, run_id=run_id, skill_root=skill_root)


def _prepare_failure(kb: Path, run_id: str, exc: Exception, stages: tuple[str, ...]) -> None:
    for stage in stages:
        name = "ANALYSIS" if stage == "analysis_prepare" else stage.upper()
        _record_failure(kb, run_id, stage, f"DIGEST_FLOW_{name}_FAILED")
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
        state["phase"] = "prepare_failed"
        _atomic_state(_state_path(kb, run_id), state)
    code = str(getattr(exc, "code", "DIGEST_FLOW_PREPARE_FAILED"))
    raise DigestFlowError(code, str(exc)) from exc


def analyze_flow(
    kb: Path, *, run_id: str, skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    work_dir = _flow_dir(kb, run_id)
    bundle_path = work_dir / "source-bundle.json"
    packet_path = work_dir / "analysis-packet.json"
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
    if state["phase"] == "prepared":
        return flow_status(kb, run_id=run_id)
    if state["phase"] != "checked":
        raise DigestFlowError("DIGEST_FLOW_STATE_INVALID", "只有已检查且有变化的来源可以准备分析。")
    try:
        record_stage(kb, run_id=run_id, stage="analysis_prepare", status="started")
        analysis = prepare_analysis_packet(
            bundle_path, packet_path, skill_root=skill_root,
            kb=kb if state["preflight_state"] != "resume_failed" else None,
        )
        plans = {}
        if "meego_changes" not in analysis or analysis["semantic_item_count"]:
            for stage in ("dependency", "semantic"):
                plans[f"{stage}_plan"] = plan_parallel_work(
                    packet_path, work_dir / stage, stage=stage, skill_root=skill_root,
                )
        record_stage(
            kb,
            run_id=run_id,
            stage="analysis_prepare",
            status="completed",
            detail_code="DIGEST_FLOW_ANALYSIS_PREPARED",
            metrics={
                "component_count": int(analysis["component_count"]),
                "input_bytes": int(analysis["input_bytes"]),
                "output_count": 1,
            },
        )
    except Exception as exc:
        _prepare_failure(kb, run_id, exc, ("analysis_prepare",))

    next_action = "candidate_generation"
    if plans:
        next_action = "dependency_review" if analysis["dependency_candidate_count"] else "semantic_analysis"
    artifacts = {
        "bundle": str(bundle_path),
        "analysis_packet": str(packet_path),
        **{name: str(plan["plan_path"]) for name, plan in plans.items()},
    }
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
        state["phase"] = "prepared"
        state["artifacts"] = artifacts
        state["next_action"] = next_action
        _atomic_state(_state_path(kb, run_id), state)
    return {
        "schema_version": FLOW_SCHEMA,
        "run_id": run_id,
        "phase": "prepared",
        "preflight_state": state["preflight_state"],
        "analysis": analysis,
        **plans,
        "next_action": next_action,
    }


def commit_flow(
    kb: Path,
    *,
    run_id: str,
    plan_path: Path,
    skill_root: Path = DEFAULT_SKILL_ROOT,
    executor: Callable[..., dict[str, Any]] = execute_plan,
) -> dict[str, Any]:
    plan = _inside_flow(kb, run_id, plan_path)
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
    if state["phase"] in {"committed", "noop"}:
        raise DigestFlowError("DIGEST_FLOW_TERMINAL", "digest flow 已终态。")
    record_stage(
        kb,
        run_id=run_id,
        stage="transaction",
        status="started",
        detail_code="DIGEST_FLOW_TRANSACTION_STARTED",
    )
    try:
        receipt = executor(kb, plan, skill_root)
        status = str(receipt.get("status", ""))
        if status not in {"committed", "noop"}:
            raise DigestFlowError(
                "DIGEST_FLOW_RECEIPT_INVALID", "transaction 未返回 committed/noop。"
            )
    except Exception as exc:
        code = str(getattr(exc, "code", "DIGEST_FLOW_COMMIT_FAILED"))
        detail = code if MACHINE_CODE_RE.fullmatch(code) else "DIGEST_FLOW_TRANSACTION_FAILED"
        _record_failure(kb, run_id, "transaction", detail)
        with _flow_lock(kb):
            state = _load_state(kb, run_id)
            state["phase"] = "commit_failed"
            _atomic_state(_state_path(kb, run_id), state)
        raise DigestFlowError(code, str(exc)) from exc

    try:
        nodes = receipt.get("nodes")
        node_count = len(nodes) if isinstance(nodes, list) else 0
        warnings = receipt.get("warnings")
        warning_count = len(warnings) if isinstance(warnings, list) else 0
        record_stage(
            kb,
            run_id=run_id,
            stage="transaction",
            status="completed",
            detail_code=f"DIGEST_FLOW_TRANSACTION_{status.upper()}",
            metrics={"node_count": node_count, "warning_count": warning_count},
        )
        finish_run(
            kb,
            run_id=run_id,
            status=status,
            metrics={"node_count": node_count, "warning_count": warning_count},
        )
    except Exception:
        receipt = {**receipt, "digest_run_logging": "degraded"}
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
        state["phase"] = status
        state["plan_path"] = str(plan)
        _atomic_state(_state_path(kb, run_id), state)
    return {
        "schema_version": FLOW_SCHEMA,
        "run_id": run_id,
        "phase": status,
        "receipt": receipt,
        "next_action": "none",
    }


def flow_status(kb: Path, *, run_id: str) -> dict[str, Any]:
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
    return {
        "schema_version": FLOW_SCHEMA,
        "run_id": run_id,
        "phase": state["phase"],
        "work_dir": str(_flow_dir(kb, run_id)),
        "artifacts": state.get("artifacts", {}),
        **({"preflight_state": state["preflight_state"]} if "preflight_state" in state else {}),
        **({"receipt": state["receipt"]} if "receipt" in state else {}),
        "next_action": state.get("next_action", "none"),
        "run": show_run(kb, run_id=run_id)["summary"],
    }
