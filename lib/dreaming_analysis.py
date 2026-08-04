"""Validate model-produced FindingBundles against committed evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_models import DreamingModelError, validate_evidence_batch, validate_finding_bundle
from dreaming_state import (
    DreamingError,
    load_state_unlocked,
    secure_path,
    state_lock,
)


MAX_FINDING_BUNDLE_BYTES = 2 * 1024 * 1024


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def load_evidence_batch(kb: Path, batch_id: str) -> dict[str, Any]:
    manifest = secure_path(kb, "batches", batch_id, "manifest.json")
    batch_path = secure_path(kb, "batches", batch_id, "batch.json")
    try:
        evidence = json.loads(manifest.read_text(encoding="utf-8"))
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_BATCH_INVALID",
            f"无法读取 batch manifest: {batch_id}",
        ) from exc
    try:
        normalized = validate_evidence_batch(evidence)
    except DreamingModelError as exc:
        raise DreamingError(exc.code, str(exc)) from exc
    if normalized["batch_id"] != batch_id or batch.get("manifest_sha256") != _sha(
        normalized
    ):
        raise DreamingError(
            "DREAMING_BATCH_INVALID",
            "EvidenceBatch id 或 manifest hash 不一致。",
        )
    return normalized


def load_finding_bundle(path: Path, *, skill_root: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    root = skill_root.resolve()
    if resolved == root or root in resolved.parents:
        raise DreamingError(
            "DREAMING_OUTPUT_IN_SKILL_REPO",
            "FindingBundle 不得位于 byteworker skill 仓库。",
        )
    try:
        if resolved.stat().st_size > MAX_FINDING_BUNDLE_BYTES:
            raise DreamingError(
                "DREAMING_FINDING_BUNDLE_TOO_LARGE",
                "FindingBundle 超过 2 MiB 上限。",
            )
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except DreamingError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_FINDING_BUNDLE_INVALID",
            "无法读取 FindingBundle JSON。",
        ) from exc
    try:
        return validate_finding_bundle(value)
    except DreamingModelError as exc:
        raise DreamingError(exc.code, str(exc)) from exc


def validate_finding_evidence(
    kb: Path,
    *,
    batch_id: str,
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = load_evidence_batch(kb, batch_id)
    if bundle.get("batch_id") != batch_id:
        raise DreamingError(
            "DREAMING_FINDING_BUNDLE_INVALID",
            "FindingBundle batch_id 与 EvidenceBatch 不一致。",
        )
    valid_refs = {str(item["item_id"]) for item in evidence["items"]}
    for finding in bundle["findings"]:
        unknown = sorted(set(finding["evidence_refs"]) - valid_refs)
        if unknown:
            raise DreamingError(
                "DREAMING_FINDING_EVIDENCE_INVALID",
                "Finding 引用了不存在的 evidence。",
                details={"finding_id": finding["finding_id"], "unknown": unknown},
            )
    with state_lock(kb):
        state = load_state_unlocked(kb, datetime.now(timezone.utc))
    revision = evidence["source"]["grant_revision"]
    run = state["runs"].get(batch_id)
    foreground_token = (
        str(run.get("foreground_token", ""))
        if isinstance(run, Mapping)
        else ""
    )
    if foreground_token:
        session = state["foreground_sessions"].get(foreground_token)
        if not isinstance(session, Mapping) or session.get("status") != "active":
            raise DreamingError(
                "DREAMING_FOREGROUND_SESSION_INVALID",
                "foreground session 已关闭。",
            )
    elif state["grants"]["revision"] != revision:
        raise DreamingError(
            "DREAMING_GRANT_STALE",
            "Finding commit 前 IM grant revision 已变化。",
        )
    return {
        "evidence": evidence,
        "bundle_sha256": _sha(bundle),
        "persist_finding": (
            False
            if foreground_token
            else bool(state["grants"]["im"]["persist_finding"])
        ),
        "foreground_token": foreground_token,
    }
