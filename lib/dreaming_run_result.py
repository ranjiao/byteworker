"""Validated, private result documents for Dreaming run auditing."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from dreaming_state import DreamingError, atomic_write_json, secure_path, utc_iso


RESULT_SCHEMA = "byteworker-dreaming-run-result/v1"
RESULT_STATUSES = {"pass", "warning", "fail", "noop"}
RUN_ID = re.compile(r"[A-Za-z0-9-]{1,96}")


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


def _review_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", f"{field} 必须是非空文本。")
    return _text(value, field, limit, required=True)


def _validate_review(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("stage") not in ("dependency_review", "conflict_review"):
        raise DreamingError(
            "DREAMING_RUN_RESULT_INVALID", "review.stage 必须是 dependency_review 或 conflict_review。"
        )
    options = value.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 5:
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "review.options 必须给出 2 至 5 个处理选项。")
    normalized_options = []
    for option in options:
        if not isinstance(option, Mapping):
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "复核选项必须包含 action 和 effect。")
        normalized_options.append({
            "action": _review_text(option.get("action"), "review.options.action", 160),
            "effect": _review_text(option.get("effect"), "review.options.effect", 500),
        })
    if len({option["action"] for option in normalized_options}) != len(normalized_options):
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "复核选项必须是不同的操作。")
    return {
        "stage": value["stage"],
        "problem": _review_text(value.get("problem"), "review.problem", 1200),
        "question": _review_text(value.get("question"), "review.question", 500),
        "options": normalized_options,
    }


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
    raw_notes = value.get("source_notes", [])
    if not isinstance(raw_notes, list) or len(raw_notes) > 200:
        raise DreamingError("DREAMING_RUN_RESULT_INVALID", "source_notes 必须是最多 200 项的数组。")
    source_notes = {}
    for note in raw_notes:
        if not isinstance(note, Mapping):
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "来源摘要格式无效。")
        key = _text(note.get("source_key"), "source_key", 64, required=True)
        if not re.fullmatch(r"[a-f0-9]{64}", key) or key in source_notes:
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "来源摘要编号无效或重复。")
        source_notes[key] = _text(note.get("summary"), "source_notes.summary", 500, required=True)
    review = _validate_review(value["review"]) if "review" in value else None
    return {
        "schema_version": RESULT_SCHEMA,
        "run_id": run_id,
        "job": job,
        "period": period,
        "summary": _text(value.get("summary"), "summary", 4000, required=True),
        "checks": checks,
        "repairs": repairs,
        **({"source_notes": source_notes} if source_notes else {}),
        **({"review": review} if review is not None else {}),
    }


def save_run_result(
    kb: Path,
    *,
    document: Mapping[str, Any],
    job: str,
    period: str,
    run_id: str,
    now,
    accounting: Mapping[str, Any] | None = None,
) -> str:
    normalized = validate_run_result(
        document,
        job=job,
        period=period,
        run_id=run_id,
    )
    normalized["recorded_at"] = utc_iso(now)
    if accounting is not None:
        source_keys = {source["source_key"] for source in accounting.get("routine_sources", [])}
        if set(normalized.get("source_notes", {})) - source_keys:
            raise DreamingError("DREAMING_RUN_RESULT_INVALID", "来源摘要引用了本轮未登记的来源。")
        normalized["accounting"] = dict(accounting)
    target = secure_path(kb, "run-results", f"{run_id}.json")
    atomic_write_json(target, normalized)
    return str(target.relative_to(kb.resolve()))


def read_run_result(kb: Path, run_id: str) -> dict[str, Any] | None:
    if not RUN_ID.fullmatch(run_id):
        return None
    try:
        path = secure_path(kb, "run-results", f"{run_id}.json")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (DreamingError, OSError, ValueError):
        return None
    if isinstance(value, dict) and value.get("schema_version") == RESULT_SCHEMA and value.get("run_id") == run_id:
        return value
    return None
