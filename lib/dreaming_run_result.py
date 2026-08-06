"""Validated, private result documents for Dreaming run auditing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from dreaming_state import DreamingError, atomic_write_json, secure_path, utc_iso


RESULT_SCHEMA = "byteworker-dreaming-run-result/v1"
RESULT_STATUSES = {"pass", "warning", "fail", "noop"}


def _text(value: object, field: str, limit: int, *, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", f"{field} 不能为空。")
    if len(normalized) > limit:
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            f"{field} 超过 {limit} 字符。",
        )
    return normalized


def validate_run_result(
    value: Mapping[str, Any],
    *,
    job: str,
    period: str,
    run_id: str,
) -> dict[str, Any]:
    if value.get("schema_version") != RESULT_SCHEMA:
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "运行结果文档 schema 无效。")
    if value.get("job") != job or value.get("period") != period:
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID",
            "运行结果文档 job/period 与当前 lease 不一致。",
        )
    raw_checks = value.get("checks", [])
    if not isinstance(raw_checks, list) or len(raw_checks) > 200:
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "checks 必须是最多 200 项的数组。")
    raw_repairs = value.get("repairs", [])
    if not isinstance(raw_repairs, list) or len(raw_repairs) > 200:
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "repairs 必须是最多 200 项的数组。")
    checks = []
    for raw in raw_checks:
        if not isinstance(raw, Mapping) or raw.get("status") not in RESULT_STATUSES:
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "运行检查项格式无效。")
        checks.append(
            {
                "name": _text(raw.get("name"), "check.name", 120, required=True),
                "status": str(raw["status"]),
                "detail": _text(raw.get("detail"), "check.detail", 2000),
            }
        )
    repairs = []
    for raw in raw_repairs:
        if not isinstance(raw, Mapping):
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "修复项格式无效。")
        repairs.append(
            {
                "path": _text(raw.get("path"), "repair.path", 512, required=True),
                "code": _text(raw.get("code"), "repair.code", 120),
                "action": _text(raw.get("action"), "repair.action", 120, required=True),
                "detail": _text(raw.get("detail"), "repair.detail", 2000),
            }
        )
    return {
        "schema_version": RESULT_SCHEMA,
        "run_id": run_id,
        "job": job,
        "period": period,
        "summary": _text(value.get("summary"), "summary", 4000, required=True),
        "checks": checks,
        "repairs": repairs,
    }


def save_run_result(
    kb: Path,
    *,
    document: Mapping[str, Any],
    job: str,
    period: str,
    run_id: str,
    now,
) -> str:
    normalized = validate_run_result(
        document,
        job=job,
        period=period,
        run_id=run_id,
    )
    normalized["recorded_at"] = utc_iso(now)
    target = secure_path(kb, "run-results", f"{run_id}.json")
    atomic_write_json(target, normalized)
    return str(target.relative_to(kb.resolve()))
