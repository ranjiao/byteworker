"""Commit Agent-produced FindingBundles into Dreaming batch state."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_analysis import (
    load_finding_bundle,
    validate_finding_evidence,
)
from dreaming_batch import commit_batch, write_stage_receipt
from dreaming_consolidation import consolidate_findings
from dreaming_state import (
    DreamingError,
    load_state_unlocked,
    secure_path,
    state_lock,
)
from dreaming_grants import close_foreground_session


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bundle_sha(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _existing_analysis(kb: Path, batch_id: str) -> dict[str, Any] | None:
    path = secure_path(kb, "batches", batch_id, "analysis.receipt.json")
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_BATCH_INVALID",
            "analysis receipt 损坏。",
        ) from exc
    return value if isinstance(value, dict) else None


def commit_finding_bundle(
    kb: Path,
    *,
    batch_id: str,
    input_path: Path,
    skill_root: Path,
    semantic_revision: str = "finding-v1",
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    bundle = load_finding_bundle(input_path, skill_root=skill_root)
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        run = state["runs"].get(batch_id)
        if not isinstance(run, Mapping):
            raise DreamingError(
                "DREAMING_BATCH_NOT_FOUND",
                f"batch 不存在: {batch_id}",
            )
        if run.get("stage") == "committed":
            marker = state["receipt_index"].get(batch_id)
            if isinstance(marker, Mapping):
                previous = _existing_analysis(kb, batch_id)
                if previous is None or previous.get("bundle_sha256") != _bundle_sha(
                    bundle
                ):
                    raise DreamingError(
                        "DREAMING_FINDING_BUNDLE_CONFLICT",
                        "同一 batch 已提交不同 FindingBundle。",
                    )
                return {**dict(marker), "status": "already_committed"}
        if run.get("stage") in {"aborted", "revoked"}:
            raise DreamingError(
                "DREAMING_BATCH_NOT_READY",
                f"batch 状态不允许 commit: {run.get('stage')}",
            )
    validation = validate_finding_evidence(
        kb,
        batch_id=batch_id,
        bundle=bundle,
    )
    evidence = validation["evidence"]
    grant_revision = evidence["source"]["grant_revision"]
    foreground_token = validation.get("foreground_token", "")
    expected_revision = None if foreground_token else grant_revision
    previous = _existing_analysis(kb, batch_id)
    if previous is not None and previous.get("bundle_sha256") != validation[
        "bundle_sha256"
    ]:
        raise DreamingError(
            "DREAMING_FINDING_BUNDLE_CONFLICT",
            "同一 batch 已提交不同 FindingBundle。",
        )
    if previous is None:
        write_stage_receipt(
            kb,
            batch_id=batch_id,
            stage="analyzed",
            receipt={
                "bundle_sha256": validation["bundle_sha256"],
                "semantic_revision": semantic_revision,
                "finding_count": len(bundle["findings"]),
            },
            expected_grant_revision=expected_revision,
            now=current,
        )
    if validation["persist_finding"]:
        consolidation = consolidate_findings(
            kb,
            batch_id=batch_id,
            bundle=bundle,
            expected_grant_revision=grant_revision,
            now=current,
        )
    else:
        consolidation = {
            "batch_id": batch_id,
            "persisted": False,
            "appended_events": 0,
            "finding_count": len(bundle["findings"]),
            "projection_count": 0,
        }
    write_stage_receipt(
        kb,
        batch_id=batch_id,
        stage="consolidated",
        receipt=consolidation,
        expected_grant_revision=expected_revision,
        now=current,
    )
    lane = (
        "discovery"
        if evidence["source"].get("collection_mode") == "all_visible"
        else "monitored"
    )
    marker = commit_batch(
        kb,
        batch_id=batch_id,
        cursor_key=f"im:{lane}",
        through=evidence["window"]["requested_end"],
        now=current,
    )
    from dreaming_reports import refresh_report_dependencies

    refresh_report_dependencies(kb, now=current)
    if foreground_token:
        close_foreground_session(
            kb,
            token=foreground_token,
            status="committed",
            now=current,
        )
    return {
        **marker,
        "persisted_findings": bool(validation["persist_finding"]),
        "finding_count": len(bundle["findings"]),
        "appended_events": consolidation["appended_events"],
    }
