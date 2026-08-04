"""Structural validators for Dreaming cross-stage contracts."""

from __future__ import annotations

from typing import Any, Mapping


EVIDENCE_BATCH_SCHEMA = "byteworker-evidence-batch/v1"
DREAMING_BATCH_SCHEMA = "byteworker-dreaming-batch/v1"
FINDING_BUNDLE_SCHEMA = "byteworker-finding-bundle/v1"
ACTION_PLAN_SCHEMA = "byteworker-action-plan/v1"
ACTION_CLAIM_SCHEMA = "byteworker-action-claim/v1"
ACTION_KINDS = {
    "suppress",
    "wait",
    "include_report",
    "instant_alert",
    "todo_candidate",
    "source_candidate",
    "conflict_review",
    "knowledge_candidate",
}


class DreamingModelError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _object(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            f"{field} 必须是 object。",
        )
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            f"{field} 必须是非空字符串。",
        )
    return value.strip()


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            f"{field} 必须是数组。",
        )
    return value


def _schema(value: object, expected: str) -> Mapping[str, Any]:
    result = _object(value, "root")
    if result.get("schema_version") != expected:
        raise DreamingModelError(
            "DREAMING_MODEL_SCHEMA_UNSUPPORTED",
            f"schema_version 必须是 {expected}。",
        )
    return result


def validate_evidence_batch(value: object) -> dict[str, Any]:
    result = _schema(value, EVIDENCE_BATCH_SCHEMA)
    _string(result.get("batch_id"), "batch_id")
    source = _object(result.get("source"), "source")
    _string(source.get("source_type"), "source.source_type")
    _string(source.get("principal"), "source.principal")
    if source.get("lane") not in {"monitored", "discovery", "foreground"}:
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "source.lane 必须是 monitored、discovery 或 foreground。",
        )
    if not isinstance(source.get("grant_revision"), int):
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "source.grant_revision 必须是整数。",
        )
    window = _object(result.get("window"), "window")
    _string(window.get("requested_start"), "window.requested_start")
    _string(window.get("requested_end"), "window.requested_end")
    coverage = _object(result.get("coverage"), "coverage")
    if coverage.get("status") not in {"complete", "partial", "best_effort"}:
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "coverage.status 必须是 complete、partial 或 best_effort。",
        )
    _list(coverage.get("gaps"), "coverage.gaps")
    for index, item_value in enumerate(_list(result.get("items"), "items")):
        item = _object(item_value, f"items[{index}]")
        _string(item.get("item_id"), f"items[{index}].item_id")
        anchor = _object(item.get("anchor"), f"items[{index}].anchor")
        _string(anchor.get("kind"), f"items[{index}].anchor.kind")
        _string(anchor.get("value"), f"items[{index}].anchor.value")
        _string(item.get("content_ref"), f"items[{index}].content_ref")
    return dict(result)


def validate_dreaming_batch(value: object) -> dict[str, Any]:
    result = _schema(value, DREAMING_BATCH_SCHEMA)
    _string(result.get("batch_id"), "batch_id")
    if result.get("stage") not in {
        "collected",
        "analyzed",
        "consolidated",
        "committed",
        "aborted",
    }:
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "stage 不受支持。",
        )
    _string(result.get("manifest_sha256"), "manifest_sha256")
    if not isinstance(result.get("grant_revision"), int):
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "grant_revision 必须是整数。",
        )
    return dict(result)


def validate_finding_bundle(value: object) -> dict[str, Any]:
    result = _schema(value, FINDING_BUNDLE_SCHEMA)
    _string(result.get("batch_id"), "batch_id")
    for index, finding_value in enumerate(
        _list(result.get("findings"), "findings")
    ):
        finding = _object(finding_value, f"findings[{index}]")
        _string(finding.get("finding_id"), f"findings[{index}].finding_id")
        if finding.get("kind") not in {
            "decision",
            "action",
            "risk",
            "change",
            "insight",
            "other",
        }:
            raise DreamingModelError(
                "DREAMING_MODEL_INVALID",
                f"findings[{index}].kind 不受支持。",
            )
        _string(finding.get("summary"), f"findings[{index}].summary")
        _string(
            finding.get("why_it_matters"),
            f"findings[{index}].why_it_matters",
        )
        if finding.get("confidence") not in {"low", "medium", "high"}:
            raise DreamingModelError(
                "DREAMING_MODEL_INVALID",
                f"findings[{index}].confidence 不受支持。",
            )
        _list(finding.get("uncertainties"), f"findings[{index}].uncertainties")
        evidence = _list(
            finding.get("evidence_refs"),
            f"findings[{index}].evidence_refs",
        )
        if not evidence:
            raise DreamingModelError(
                "DREAMING_MODEL_INVALID",
                f"findings[{index}].evidence_refs 不能为空。",
            )
        for evidence_index, reference in enumerate(evidence):
            _string(
                reference,
                f"findings[{index}].evidence_refs[{evidence_index}]",
            )
    return dict(result)


def validate_action_plan(value: object) -> dict[str, Any]:
    result = _schema(value, ACTION_PLAN_SCHEMA)
    _string(result.get("run_id"), "run_id")
    for index, action_value in enumerate(_list(result.get("actions"), "actions")):
        action = _object(action_value, f"actions[{index}]")
        _string(action.get("action_id"), f"actions[{index}].action_id")
        kind = _string(action.get("kind"), f"actions[{index}].kind")
        if kind not in ACTION_KINDS:
            raise DreamingModelError(
                "DREAMING_MODEL_INVALID",
                f"actions[{index}].kind 不受支持。",
            )
        _string(action.get("dedupe_key"), f"actions[{index}].dedupe_key")
        _string(action.get("finding_id"), f"actions[{index}].finding_id")
        evidence = _list(
            action.get("evidence_refs"),
            f"actions[{index}].evidence_refs",
        )
        if not evidence:
            raise DreamingModelError(
                "DREAMING_MODEL_INVALID",
                f"actions[{index}].evidence_refs 不能为空。",
            )
        for field in ("requires_confirmation", "requires_recapture"):
            if not isinstance(action.get(field), bool):
                raise DreamingModelError(
                    "DREAMING_MODEL_INVALID",
                    f"actions[{index}].{field} 必须是布尔值。",
                )
        if action.get("policy_result") not in {"allowed", "denied", "confirm"}:
            raise DreamingModelError(
                "DREAMING_MODEL_INVALID",
                f"actions[{index}].policy_result 不受支持。",
            )
    return dict(result)


def validate_action_claim(value: object) -> dict[str, Any]:
    result = _schema(value, ACTION_CLAIM_SCHEMA)
    for field in ("action_id", "run_id", "job", "period", "token"):
        _string(result.get(field), field)
    if not isinstance(result.get("lease_epoch"), int):
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "lease_epoch 必须是整数。",
        )
    if result.get("status") not in {
        "planned",
        "claimed",
        "committed",
        "cancelled",
        "reconcile",
    }:
        raise DreamingModelError(
            "DREAMING_MODEL_INVALID",
            "status 不受支持。",
        )
    return dict(result)


VALIDATORS = {
    EVIDENCE_BATCH_SCHEMA: validate_evidence_batch,
    DREAMING_BATCH_SCHEMA: validate_dreaming_batch,
    FINDING_BUNDLE_SCHEMA: validate_finding_bundle,
    ACTION_PLAN_SCHEMA: validate_action_plan,
    ACTION_CLAIM_SCHEMA: validate_action_claim,
}


def validate_contract(value: object) -> dict[str, Any]:
    result = _object(value, "root")
    schema = result.get("schema_version")
    validator = VALIDATORS.get(schema)
    if validator is None:
        raise DreamingModelError(
            "DREAMING_MODEL_SCHEMA_UNSUPPORTED",
            f"不支持的 Dreaming schema: {schema!r}",
        )
    return validator(result)
