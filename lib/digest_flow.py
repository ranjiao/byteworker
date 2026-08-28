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
from digest_capture import execute_capture_plan
from digest_parallel import plan_parallel_work
from digest_run_log import (
    finish_run,
    record_stage,
    show_run,
    start_run,
)
from digest_txn import execute_plan, preflight_bundle
from source_bundle_request import build_bundle_from_request
from source_capture import write_capture


FLOW_SCHEMA = "byteworker-digest-flow/v1"
DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]
FLOW_PHASES = {
    "classified",
    "captured",
    "capture_failed",
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
        _record_failure(kb, run_id, "capture", "DIGEST_FLOW_CAPTURE_FAILED")
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
    bundle_request: Path,
    skill_root: Path = DEFAULT_SKILL_ROOT,
) -> dict[str, Any]:
    request = _inside_flow(kb, run_id, bundle_request)
    work_dir = _flow_dir(kb, run_id)
    bundle_path = work_dir / "source-bundle.json"
    packet_path = work_dir / "analysis-packet.json"
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
        bundle = build_bundle_from_request(
            str(state["source_type"]), request, skill_root=skill_root
        )
        write_capture(bundle_path, bundle.to_dict(), skill_root=skill_root)
        record_stage(
            kb,
            run_id=run_id,
            stage="bundle",
            status="completed",
            detail_code="DIGEST_FLOW_BUNDLE_COMPLETED",
            metrics={"component_count": len(bundle.components)},
        )

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
            finish_run(kb, run_id=run_id, status="noop")
            with _flow_lock(kb):
                state = _load_state(kb, run_id)
                state["phase"] = "noop"
                state["artifacts"] = {"bundle": str(bundle_path)}
                _atomic_state(_state_path(kb, run_id), state)
            return {
                "schema_version": FLOW_SCHEMA,
                "run_id": run_id,
                "phase": "noop",
                "preflight_state": "noop",
                "next_action": "none",
            }

        record_stage(kb, run_id=run_id, stage="analysis_prepare", status="started")
        analysis = prepare_analysis_packet(
            bundle_path, packet_path, skill_root=skill_root
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
        dependency_plan = plan_parallel_work(
            packet_path, work_dir / "dependency", stage="dependency", skill_root=skill_root
        )
        semantic_plan = plan_parallel_work(
            packet_path, work_dir / "semantic", stage="semantic", skill_root=skill_root
        )
    except Exception as exc:
        for stage, code in (
            ("analysis_prepare", "DIGEST_FLOW_ANALYSIS_FAILED"),
            ("preflight", "DIGEST_FLOW_PREFLIGHT_FAILED"),
            ("bundle", "DIGEST_FLOW_BUNDLE_FAILED"),
        ):
            _record_failure(kb, run_id, stage, code)
        with _flow_lock(kb):
            state = _load_state(kb, run_id)
            state["phase"] = "prepare_failed"
            _atomic_state(_state_path(kb, run_id), state)
        code = str(getattr(exc, "code", "DIGEST_FLOW_PREPARE_FAILED"))
        raise DigestFlowError(code, str(exc)) from exc

    next_action = (
        "dependency_review"
        if int(analysis["dependency_candidate_count"]) > 0
        else "semantic_analysis"
    )
    artifacts = {
        "bundle": str(bundle_path),
        "analysis_packet": str(packet_path),
        "dependency_plan": str(dependency_plan["plan_path"]),
        "semantic_plan": str(semantic_plan["plan_path"]),
    }
    with _flow_lock(kb):
        state = _load_state(kb, run_id)
        state["phase"] = "prepared"
        state["artifacts"] = artifacts
        state["preflight_state"] = flight["state"]
        state["next_action"] = next_action
        _atomic_state(_state_path(kb, run_id), state)
    return {
        "schema_version": FLOW_SCHEMA,
        "run_id": run_id,
        "phase": "prepared",
        "preflight_state": flight["state"],
        "analysis": analysis,
        "dependency_plan": dependency_plan,
        "semantic_plan": semantic_plan,
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
        nodes = receipt.get("nodes")
        node_count = len(nodes) if isinstance(nodes, list) else 0
        warnings = receipt.get("warnings")
        warning_count = len(warnings) if isinstance(warnings, list) else 0
        record_stage(
            kb,
            run_id=run_id,
            stage="transaction",
            status="completed",
            detail_code="DIGEST_FLOW_TRANSACTION_COMPLETED",
            metrics={"node_count": node_count, "warning_count": warning_count},
        )
        finish_run(
            kb,
            run_id=run_id,
            status=status,
            metrics={"node_count": node_count, "warning_count": warning_count},
        )
    except Exception as exc:
        _record_failure(kb, run_id, "transaction", "DIGEST_FLOW_TRANSACTION_FAILED")
        with _flow_lock(kb):
            state = _load_state(kb, run_id)
            state["phase"] = "commit_failed"
            _atomic_state(_state_path(kb, run_id), state)
        code = str(getattr(exc, "code", "DIGEST_FLOW_COMMIT_FAILED"))
        raise DigestFlowError(code, str(exc)) from exc
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
        "next_action": state.get("next_action", "none"),
        "run": show_run(kb, run_id=run_id)["summary"],
    }
