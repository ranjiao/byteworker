"""Deterministic policy validation for model-proposed Dreaming actions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_analysis import load_evidence_batch
from dreaming_consolidation import load_findings
from dreaming_models import DreamingModelError, validate_action_plan
from dreaming_state import DreamingError, load_state_unlocked, state_lock


MAX_ACTION_PLAN_BYTES = 1024 * 1024
CONFIRMATION_KINDS = {"todo_candidate", "source_candidate", "conflict_review"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def plan_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def load_action_plan(path: Path, *, skill_root: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    root = skill_root.resolve()
    if resolved == root or root in resolved.parents:
        raise DreamingError(
            "DREAMING_OUTPUT_IN_SKILL_REPO",
            "ActionPlan 不得位于 byteworker skill 仓库。",
        )
    try:
        if resolved.stat().st_size > MAX_ACTION_PLAN_BYTES:
            raise DreamingError(
                "DREAMING_ACTION_PLAN_TOO_LARGE",
                "ActionPlan 超过 1 MiB 上限。",
            )
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except DreamingError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_ACTION_PLAN_INVALID",
            "无法读取 ActionPlan JSON。",
        ) from exc
    try:
        return validate_action_plan(value)
    except DreamingModelError as exc:
        raise DreamingError(exc.code, str(exc)) from exc


def _finding_coverage(kb: Path, finding: Mapping[str, Any]) -> dict[str, Any]:
    batches = []
    missing = []
    for batch_id in finding.get("batch_ids", []):
        try:
            evidence = load_evidence_batch(kb, str(batch_id))
        except DreamingError:
            missing.append(str(batch_id))
            continue
        batches.append(
            {
                "batch_id": batch_id,
                "lane": evidence["source"]["lane"],
                "coverage": evidence["coverage"]["status"],
            }
        )
    return {
        "batches": batches,
        "missing": missing,
        "complete_monitored": bool(batches)
        and not missing
        and all(
            value["lane"] == "monitored" and value["coverage"] == "complete"
            for value in batches
        ),
    }


def evaluate_action_plan(
    kb: Path,
    *,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    findings = load_findings(kb)["findings"]
    with state_lock(kb):
        state = load_state_unlocked(kb, datetime.now(timezone.utc))
    grants = state["grants"]["actions"]
    normalized_actions = []
    for proposed in plan["actions"]:
        action = dict(proposed)
        finding = findings.get(action["finding_id"])
        reasons = []
        allowed = True
        if not isinstance(finding, Mapping):
            allowed = False
            reasons.append("finding_not_found")
            finding = {}
        unknown_evidence = sorted(
            set(action["evidence_refs"]) - set(finding.get("evidence_refs", []))
        )
        if unknown_evidence:
            allowed = False
            reasons.append("evidence_not_in_finding")
        coverage = _finding_coverage(kb, finding)
        kind = action["kind"]
        requires_confirmation = kind in CONFIRMATION_KINDS
        requires_recapture = kind == "knowledge_candidate"
        if kind == "include_report":
            if action.get("target") not in {"morning", "daily", "weekly"}:
                allowed = False
                reasons.append("report_target_invalid")
            if not grants["persist_report"]:
                allowed = False
                reasons.append("persist_report_grant_required")
        elif kind == "instant_alert" and not grants["instant_alert"]:
            allowed = False
            reasons.append("instant_alert_grant_required")
        elif kind == "knowledge_candidate":
            if not grants["archive"]:
                allowed = False
                reasons.append("archive_grant_required")
            if not coverage["complete_monitored"]:
                allowed = False
                reasons.append("complete_monitored_evidence_required")
        if allowed and requires_confirmation:
            policy_result = "confirm"
        elif allowed:
            policy_result = "allowed"
        else:
            policy_result = "denied"
        action.update(
            {
                "policy_result": policy_result,
                "policy_reasons": reasons,
                "requires_confirmation": requires_confirmation,
                "requires_recapture": requires_recapture,
                "coverage": coverage,
            }
        )
        normalized_actions.append(action)
    result = {
        "schema_version": "byteworker-action-plan/v1",
        "run_id": plan["run_id"],
        "grant_revision": state["grants"]["revision"],
        "actions": normalized_actions,
    }
    return {**result, "plan_sha256": plan_sha256(result)}
