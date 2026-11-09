"""Validated, private result documents for Dreaming run auditing."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from dreaming_state import DreamingError, atomic_write_json, secure_path, utc_iso


RESULT_SCHEMA = "byteworker-dreaming-run-result/v1"
RESULT_STATUSES = {"pass", "warning", "fail", "noop"}


def validate_maintenance_issues(raw_issues: object) -> list[dict[str, str]]:
    if not isinstance(raw_issues, list):
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "issues 必须是数组。")
    issues = {}
    for issue in raw_issues:
        if not isinstance(issue, Mapping):
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "维护问题格式无效。")
        issue_id = _text(issue.get("id"), "issue.id", 64, required=True)
        code = _text(issue.get("code"), "issue.code", 120, required=True)
        path = _text(issue.get("path"), "issue.path", 512, required=True)
        severity = issue.get("severity")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", issue_id)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]+", code)
            or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
            or any(c in path for c in "\\:\n\r\0")
            or severity not in ("error", "warning")
            or issue_id in issues
        ):
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "维护问题标识或路径无效。")
        issues[issue_id] = {"id": issue_id, "code": code, "path": path, "severity": severity}
    return list(issues.values())


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
