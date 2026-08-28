"""Resolve workflow instruction closures and enforce explicit token budgets."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


ROUTE_SCHEMA = "byteworker-workflow-routes/v2"
BUDGET_RECEIPT_SCHEMA = "byteworker-workflow-budget-receipt/v1"
ESTIMATOR_METHOD = "byteworker-conservative-token-estimate/v1"
TOKENIZER_METHOD = "tiktoken:o200k_base"
ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "references" / "workflow-routes.json"


class WorkflowBudgetError(RuntimeError):
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


def _conservative_tokens(text: str) -> int:
    """Estimate tokens conservatively without a third-party runtime dependency."""

    total = 0
    index = 0
    while index < len(text):
        character = text[index]
        if character.isascii() and (character.isalnum() or character == "_"):
            end = index + 1
            while end < len(text) and text[end].isascii() and (
                text[end].isalnum() or text[end] == "_"
            ):
                end += 1
            total += math.ceil((end - index) / 3)
            index = end
            continue
        if character.isascii() and character.isspace():
            end = index + 1
            while end < len(text) and text[end].isascii() and text[end].isspace():
                end += 1
            total += math.ceil((end - index) / 4)
            index = end
            continue
        if character.isascii():
            total += 1
        elif character.isspace():
            total += 1
        else:
            total += max(2, math.ceil(len(character.encode("utf-8")) / 2))
        index += 1
    return total


def estimate_tokens(text: str) -> dict[str, Any]:
    """Use a fixed tokenizer when installed, otherwise identify the estimate."""

    try:
        import tiktoken  # type: ignore[import-not-found]

        encoding = tiktoken.get_encoding("o200k_base")
    except (ImportError, AttributeError, KeyError, ValueError):
        return {
            "tokens": _conservative_tokens(text),
            "method": ESTIMATOR_METHOD,
            "exact": False,
        }
    return {
        "tokens": len(encoding.encode(text, disallowed_special=())),
        "method": TOKENIZER_METHOD,
        "exact": True,
    }


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowBudgetError(
            "WORKFLOW_MANIFEST_INVALID", "workflow route manifest 无法读取。"
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != ROUTE_SCHEMA:
        raise WorkflowBudgetError(
            "WORKFLOW_MANIFEST_INVALID", f"workflow route manifest 必须为 {ROUTE_SCHEMA}。"
        )
    if not isinstance(value.get("workflows"), dict) or not isinstance(
        value.get("budgets"), dict
    ):
        raise WorkflowBudgetError(
            "WORKFLOW_MANIFEST_INVALID", "workflow route manifest 缺少 workflows/budgets。"
        )
    return value


def _lineage(
    workflows: Mapping[str, Any], workflow: str, stack: tuple[str, ...] = ()
) -> list[tuple[str, Mapping[str, Any]]]:
    if workflow in stack:
        raise WorkflowBudgetError(
            "WORKFLOW_ROUTE_CYCLE", "workflow extends 存在循环。"
        )
    value = workflows.get(workflow)
    if not isinstance(value, Mapping):
        raise WorkflowBudgetError(
            "WORKFLOW_UNKNOWN", f"未知 workflow: {workflow}"
        )
    result: list[tuple[str, Mapping[str, Any]]] = []
    parent = value.get("extends")
    if parent:
        result.extend(_lineage(workflows, str(parent), stack + (workflow,)))
    result.append((workflow, value))
    return result


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def resolve_route(
    manifest: Mapping[str, Any],
    *,
    workflow: str,
    source_type: str = "",
    features: Iterable[str] = (),
    include_on_error: bool = False,
) -> dict[str, Any]:
    workflows = manifest["workflows"]
    lineage = _lineage(workflows, workflow)
    selected_features = _unique(str(item) for item in features)
    required: list[str] = []
    source_paths: list[str] = []
    feature_paths: list[str] = []
    error_paths: list[str] = []
    prompt_paths: list[str] = []
    known_sources: set[str] = set()
    known_features: set[str] = set()
    include_router = True
    for _, value in lineage:
        include_router = bool(value.get("include_router", include_router))
        required.extend(map(str, value.get("required", [])))
        prompt_paths.extend(map(str, value.get("worker_prompt", [])))
        source_map = value.get("source_type", {})
        feature_map = value.get("features", {})
        if not isinstance(source_map, Mapping) or not isinstance(feature_map, Mapping):
            raise WorkflowBudgetError(
                "WORKFLOW_MANIFEST_INVALID", "source_type/features 必须为 object。"
            )
        known_sources.update(map(str, source_map))
        known_features.update(map(str, feature_map))
        if source_type and source_type in source_map:
            source_paths.extend(map(str, source_map[source_type]))
        for feature in selected_features:
            if feature in feature_map:
                feature_paths.extend(map(str, feature_map[feature]))
        if include_on_error:
            error_paths.extend(map(str, value.get("on_error", [])))
    if source_type and source_type not in known_sources:
        raise WorkflowBudgetError(
            "WORKFLOW_SOURCE_TYPE_INVALID",
            f"workflow {workflow} 不支持 source_type={source_type}。",
        )
    unknown_features = sorted(set(selected_features) - known_features)
    if unknown_features:
        raise WorkflowBudgetError(
            "WORKFLOW_FEATURE_INVALID",
            "workflow feature 非法。",
            details={"features": unknown_features},
        )
    static_paths = (["SKILL.md"] if include_router else []) + required
    return {
        "workflow": workflow,
        "source_type": source_type,
        "features": selected_features,
        "include_on_error": include_on_error,
        "static_rule_paths": _unique(
            [*static_paths, *source_paths, *feature_paths, *error_paths]
        ),
        "worker_prompt_paths": _unique(prompt_paths),
    }


def _safe_text(root: Path, relative: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", relative):
        raise WorkflowBudgetError(
            "WORKFLOW_PATH_INVALID", f"manifest path 非法: {relative}"
        )
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkflowBudgetError(
            "WORKFLOW_PATH_INVALID", f"manifest path 越界: {relative}"
        ) from exc
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowBudgetError(
            "WORKFLOW_PATH_MISSING", f"manifest path 不存在: {relative}"
        ) from exc


def _measure_texts(texts: Iterable[str]) -> dict[str, Any]:
    values = list(texts)
    measured = estimate_tokens("\n\n".join(values))
    return {
        **measured,
        "characters": sum(len(value) for value in values),
    }


def _external_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.expanduser().resolve().read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowBudgetError(
            "WORKFLOW_DYNAMIC_INPUT_INVALID", "动态预算输入无法读取。"
        ) from exc


def inspect_budget(
    *,
    workflow: str,
    source_type: str = "",
    features: Iterable[str] = (),
    include_on_error: bool = False,
    context_path: Path | None = None,
    source_packet_path: Path | None = None,
    root: Path = ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    route = resolve_route(
        manifest,
        workflow=workflow,
        source_type=source_type,
        features=features,
        include_on_error=include_on_error,
    )
    budget = manifest["budgets"].get(workflow)
    required_budget_fields = {
        "static_rules_tokens",
        "worker_prompt_tokens",
        "dynamic_context_tokens",
        "source_packet_tokens",
        "total_input_tokens",
        "output_tokens",
        "overflow_action",
    }
    if not isinstance(budget, Mapping) or not required_budget_fields.issubset(budget):
        raise WorkflowBudgetError(
            "WORKFLOW_BUDGET_MISSING", f"workflow {workflow} 缺少完整预算。"
        )
    numeric_fields = required_budget_fields - {"overflow_action"}
    if any(
        not isinstance(budget[field], int)
        or isinstance(budget[field], bool)
        or int(budget[field]) < 0
        for field in numeric_fields
    ):
        raise WorkflowBudgetError(
            "WORKFLOW_BUDGET_INVALID", f"workflow {workflow} 预算必须为非负整数。"
        )
    static = _measure_texts(
        _safe_text(root, path) for path in route["static_rule_paths"]
    )
    worker_prompt = _measure_texts(
        _safe_text(root, path) for path in route["worker_prompt_paths"]
    )
    context = _measure_texts([_external_text(context_path)])
    source_packet = _measure_texts([_external_text(source_packet_path)])
    actual = {
        "static_rules_tokens": static["tokens"],
        "worker_prompt_tokens": worker_prompt["tokens"],
        "dynamic_context_tokens": context["tokens"],
        "source_packet_tokens": source_packet["tokens"],
    }
    actual["total_input_tokens"] = sum(actual.values())
    checks = {
        field: actual[field] <= int(budget[field])
        for field in actual
    }
    reserved_total = sum(
        int(budget[field])
        for field in (
            "static_rules_tokens",
            "worker_prompt_tokens",
            "dynamic_context_tokens",
            "source_packet_tokens",
        )
    )
    checks["reserved_total_tokens"] = reserved_total <= int(
        budget["total_input_tokens"]
    )
    within_budget = all(checks.values())
    methods = sorted(
        {
            static["method"],
            worker_prompt["method"],
            context["method"],
            source_packet["method"],
        }
    )
    return {
        "schema_version": BUDGET_RECEIPT_SCHEMA,
        "status": "within_budget" if within_budget else "over_budget",
        "workflow": workflow,
        "route": route,
        "measurement": {
            "method": methods[0] if len(methods) == 1 else methods,
            "exact": bool(
                static["exact"]
                and worker_prompt["exact"]
                and context["exact"]
                and source_packet["exact"]
            ),
            "static_rules_characters": static["characters"],
            "worker_prompt_characters": worker_prompt["characters"],
            **actual,
        },
        "budget": {**dict(budget), "reserved_total_tokens": reserved_total},
        "checks": checks,
        "action": "continue" if within_budget else str(budget["overflow_action"]),
    }
