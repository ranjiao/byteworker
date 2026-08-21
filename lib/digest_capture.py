"""Bounded parallel execution for read-only digest capture jobs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


CAPTURE_PLAN_SCHEMA = "byteworker-digest-capture-plan/v1"
CAPTURE_RECEIPT_SCHEMA = "byteworker-digest-capture-receipt/v1"
MAX_CAPTURE_JOBS = 16
MAX_CAPTURE_WORKERS = 4
MAX_ATTEMPTS = 3
MAX_TIMEOUT_SECONDS = 900
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DEFAULT_SKILL_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_SCRIPTS = {"pull_doc_comments.py"}
READ_ONLY_LARK_OPERATIONS = {
    ("docs", "+fetch"),
    ("whiteboard", "+export"),
    ("minutes", "+detail"),
    ("minutes", "+search"),
    ("minutes", "minutes"),
    ("vc", "+detail"),
    ("vc", "+recording"),
    ("vc", "+search"),
    ("vc", "meeting"),
}


class DigestCaptureError(RuntimeError):
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


def _outside_skill(path: Path, *, skill_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    root = skill_root.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    raise DigestCaptureError(
        "DIGEST_CAPTURE_PATH_IN_SKILL_REPO",
        "capture plan 和输出不得位于 byteworker skill 仓库。",
        details={"path": str(resolved)},
    )


def _load_request(path: Path, *, skill_root: Path) -> Mapping[str, Any]:
    request_path = _outside_skill(path, skill_root=skill_root)
    try:
        value = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DigestCaptureError(
            "DIGEST_CAPTURE_PLAN_INVALID", "capture plan 无法读取或不是合法 JSON。"
        ) from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != CAPTURE_PLAN_SCHEMA:
        raise DigestCaptureError(
            "DIGEST_CAPTURE_PLAN_INVALID",
            f"schema_version 必须为 {CAPTURE_PLAN_SCHEMA}。",
        )
    return value


def _validated_jobs(
    request: Mapping[str, Any], *, skill_root: Path
) -> tuple[int, list[dict[str, Any]]]:
    workers = request.get("max_workers", 3)
    if not isinstance(workers, int) or isinstance(workers, bool) or not 1 <= workers <= MAX_CAPTURE_WORKERS:
        raise DigestCaptureError(
            "DIGEST_CAPTURE_PLAN_INVALID",
            f"max_workers 必须为 1-{MAX_CAPTURE_WORKERS}。",
        )
    raw_jobs = request.get("jobs")
    if not isinstance(raw_jobs, list) or not 1 <= len(raw_jobs) <= MAX_CAPTURE_JOBS:
        raise DigestCaptureError(
            "DIGEST_CAPTURE_PLAN_INVALID",
            f"jobs 必须为 1-{MAX_CAPTURE_JOBS} 项。",
        )
    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_outputs: set[Path] = set()
    for index, raw in enumerate(raw_jobs):
        if not isinstance(raw, Mapping):
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID", f"jobs[{index}] 必须为 object。"
            )
        job_id = str(raw.get("id", ""))
        if not JOB_ID_RE.fullmatch(job_id) or job_id in seen_ids:
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID", f"jobs[{index}].id 非法或重复。"
            )
        runner = raw.get("runner")
        if runner not in {"lark", "comments"}:
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID",
                f"jobs[{index}].runner 只支持 lark/comments。",
            )
        args = raw.get("args")
        if (
            not isinstance(args, list)
            or not args
            or any(not isinstance(item, str) or "\x00" in item for item in args)
        ):
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID", f"jobs[{index}].args 必须为非空字符串数组。"
            )
        if runner == "lark" and (
            len(args) < 2
            or tuple(args[:2]) not in READ_ONLY_LARK_OPERATIONS
            or "--yes" in args
        ):
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID",
                f"jobs[{index}] 不是 allowlist 内的 lark 只读操作。",
            )
        raw_output = Path(str(raw.get("output", ""))).expanduser()
        if not raw_output.is_absolute() or not raw_output.name:
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID", f"jobs[{index}].output 必须为绝对文件路径。"
            )
        output = _outside_skill(raw_output, skill_root=skill_root)
        if output in seen_outputs:
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID", f"jobs[{index}].output 重复。"
            )
        attempts = raw.get("max_attempts", 2)
        timeout = raw.get("timeout_seconds", 180)
        if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= MAX_ATTEMPTS:
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID",
                f"jobs[{index}].max_attempts 必须为 1-{MAX_ATTEMPTS}。",
            )
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise DigestCaptureError(
                "DIGEST_CAPTURE_PLAN_INVALID",
                f"jobs[{index}].timeout_seconds 必须为 1-{MAX_TIMEOUT_SECONDS}。",
            )
        seen_ids.add(job_id)
        seen_outputs.add(output)
        jobs.append(
            {
                "id": job_id,
                "runner": runner,
                "args": list(args),
                "output": output,
                "max_attempts": attempts,
                "timeout_seconds": timeout,
            }
        )
    return workers, jobs


def _command(job: Mapping[str, Any], *, skill_root: Path) -> list[str]:
    if job["runner"] == "lark":
        return [os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli"), *job["args"]]
    script = "pull_doc_comments.py"
    if script not in ALLOWED_SCRIPTS:
        raise DigestCaptureError("DIGEST_CAPTURE_PLAN_INVALID", "capture script 未获允许。")
    return [sys.executable, str(skill_root / "bin" / script), *job["args"]]


def _run_one(
    job: Mapping[str, Any],
    *,
    skill_root: Path,
    runner: Callable[..., Any],
    sleeper: Callable[[float], None],
) -> dict[str, Any]:
    output = Path(job["output"])
    if output.is_symlink() or output.parent.is_symlink():
        raise DigestCaptureError(
            "DIGEST_CAPTURE_PATH_UNSAFE",
            "capture 输出或父目录不能是符号链接。",
            details={"job_id": job["id"]},
        )
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    command = _command(job, skill_root=skill_root)
    started = time.monotonic()
    attempts = 0
    last_returncode = -1
    for attempt in range(1, int(job["max_attempts"]) + 1):
        attempts = attempt
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".capture-{job['id']}-", dir=output.parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                try:
                    completed = runner(
                        command,
                        stdout=handle,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=int(job["timeout_seconds"]),
                    )
                    last_returncode = int(completed.returncode)
                except (OSError, subprocess.TimeoutExpired):
                    last_returncode = -1
                handle.flush()
                os.fsync(handle.fileno())
            if last_returncode == 0:
                os.replace(temporary, output)
                os.chmod(output, 0o600)
                return {
                    "id": job["id"],
                    "status": "completed",
                    "attempts": attempts,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "output_bytes": output.stat().st_size,
                    "output": str(output),
                }
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        if attempt < int(job["max_attempts"]):
            sleeper(float(2 ** (attempt - 1)))
    try:
        output.unlink()
    except FileNotFoundError:
        pass
    return {
        "id": job["id"],
        "status": "failed",
        "attempts": attempts,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "returncode": last_returncode,
    }


def execute_capture_plan(
    request_path: Path,
    *,
    skill_root: Path = DEFAULT_SKILL_ROOT,
    runner: Callable[..., Any] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute a bounded set of independent read-only capture jobs."""

    request = _load_request(request_path, skill_root=skill_root)
    max_workers, jobs = _validated_jobs(request, skill_root=skill_root)
    request_resolved = request_path.expanduser().resolve()
    if any(Path(job["output"]) == request_resolved for job in jobs):
        raise DigestCaptureError(
            "DIGEST_CAPTURE_PLAN_INVALID", "capture output 不能覆盖 request plan。"
        )
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as executor:
        futures = {
            executor.submit(
                _run_one,
                job,
                skill_root=skill_root,
                runner=runner,
                sleeper=sleeper,
            ): job["id"]
            for job in jobs
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except DigestCaptureError as exc:
                results.append(
                    {"id": futures[future], "status": "failed", "error_code": exc.code}
                )
    results.sort(key=lambda item: item["id"])
    failed = [item["id"] for item in results if item["status"] != "completed"]
    receipt = {
        "schema_version": CAPTURE_RECEIPT_SCHEMA,
        "status": "completed" if not failed else "failed",
        "max_workers": max_workers,
        "job_count": len(jobs),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "output_bytes": sum(int(item.get("output_bytes", 0)) for item in results),
        "failed_job_ids": failed,
        "jobs": results,
    }
    if failed:
        raise DigestCaptureError(
            "DIGEST_CAPTURE_JOB_FAILED",
            "一个或多个 capture job 失败，未生成完整来源覆盖。",
            details={"failed_job_ids": failed, "receipt": receipt},
        )
    return receipt
