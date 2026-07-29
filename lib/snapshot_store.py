"""Load persisted structured-source snapshots from a byteworker KB.

The snapshot store is deliberately strict.  A raw file that declares the
requested source identity is part of that source's history, so malformed or
ambiguous snapshot content must fail closed instead of silently disappearing
from a routine diff.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from frontmatter import parse_file
from source_capture import (
    SNAPSHOT_SCHEMA,
    SourceCaptureError,
    diff_captures,
)


JSON_FENCE_RE = re.compile(r"(`{3,})json\s*")


@dataclass(frozen=True)
class RawSnapshotProvenance:
    """Stable metadata identifying the raw artifact that owns a snapshot."""

    raw_id: str
    raw_path: str
    ingested: str
    source_type: str
    source_uid: str
    source_url: str = ""
    source_title: str = ""
    source_revision: str = ""
    raw_content_hash: str = ""
    digest_status: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_id": self.raw_id,
            "raw_path": self.raw_path,
            "ingested": self.ingested,
            "source_type": self.source_type,
            "source_uid": self.source_uid,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "source_revision": self.source_revision,
            "raw_content_hash": self.raw_content_hash,
            "digest_status": self.digest_status,
        }


@dataclass(frozen=True)
class PersistedSnapshot:
    """A validated snapshot and the raw artifact it was loaded from."""

    snapshot: Mapping[str, Any]
    provenance: RawSnapshotProvenance
    snapshot_hash: str
    identity_inferred: bool = False

    def as_capture(self) -> dict[str, Any]:
        """Return the minimal capture wrapper accepted by ``diff_captures``."""
        return {
            "content_hash": self.snapshot_hash,
            "snapshot": dict(self.snapshot),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": dict(self.snapshot),
            "snapshot_hash": self.snapshot_hash,
            "identity_inferred": self.identity_inferred,
            "raw": self.provenance.as_dict(),
        }


def _error(
    code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> SourceCaptureError:
    return SourceCaptureError(code, message, details=details)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _ingested_sort_key(value: str, *, path: Path) -> tuple[float, str]:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_RAW",
            f"raw 的 ingested 不是合法 ISO 时间: {path}",
            details={"ingested": value},
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.timestamp(), path.name)


def _json_code_blocks(body: str, *, path: Path) -> Iterable[Any]:
    """Yield JSON fences with the same Markdown semantics as KB querying."""
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        match = JSON_FENCE_RE.fullmatch(lines[index].strip())
        if not match:
            index += 1
            continue
        fence = match.group(1)
        start = index + 1
        index = start
        while index < len(lines) and lines[index].strip() != fence:
            index += 1
        if index >= len(lines):
            raise _error(
                "SOURCE_SNAPSHOT_INVALID_RAW",
                f"raw 中的 JSON code fence 未闭合: {path}",
            )
        text = "\n".join(lines[start:index]).strip()
        if not text:
            raise _error(
                "SOURCE_SNAPSHOT_INVALID_RAW",
                f"raw 中存在空 JSON code fence: {path}",
            )
        try:
            yield json.loads(text)
        except json.JSONDecodeError as exc:
            raise _error(
                "SOURCE_SNAPSHOT_INVALID_RAW",
                f"raw 中的结构化 JSON 无法解析: {path}",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc
        index += 1


def _snapshot_candidates(body: str, *, path: Path) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    for value in _json_code_blocks(body, path=path):
        if not isinstance(value, Mapping):
            continue
        nested = value.get("snapshot")
        candidate = nested if isinstance(nested, Mapping) else value
        if candidate.get("schema_version") == SNAPSHOT_SCHEMA:
            candidates.append(candidate)
    if not candidates:
        raise _error(
            "SOURCE_SNAPSHOT_MISSING",
            f"raw 中找不到 {SNAPSHOT_SCHEMA} 完整快照: {path}",
        )
    if len(candidates) > 1:
        raise _error(
            "SOURCE_SNAPSHOT_AMBIGUOUS",
            f"raw 中包含多个 {SNAPSHOT_SCHEMA} 快照: {path}",
        )
    return candidates


def _ensure_committed_raw(kb: Path, path: Path) -> None:
    """Reject dirty/untracked snapshots when the KB has its required Git repo."""

    if not (kb / ".git").is_dir():
        return
    relative = str(path.resolve().relative_to(kb.resolve()))
    tracked = subprocess.run(
        ["git", "-C", str(kb), "ls-files", "--error-unmatch", "--", relative],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    status = subprocess.run(
        ["git", "-C", str(kb), "status", "--porcelain=v1", "--", relative],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if tracked.returncode != 0 or status.returncode != 0 or status.stdout.strip():
        raise _error(
            "SOURCE_SNAPSHOT_UNCOMMITTED_RAW",
            f"SnapshotStore 只读取已提交且干净的 raw: {path}",
            details={"raw_path": relative},
        )


def _load_raw_snapshot(
    kb: Path,
    path: Path,
    *,
    source_type: str,
    source_uid: str,
) -> PersistedSnapshot:
    _ensure_committed_raw(kb, path)
    try:
        frontmatter, body = parse_file(str(path))
    except (OSError, UnicodeError) as exc:
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_RAW",
            f"无法读取 raw: {path}",
        ) from exc

    raw_id = str(frontmatter.get("raw_id", "")).strip()
    ingested = str(frontmatter.get("ingested", "")).strip()
    raw_source_type = str(frontmatter.get("source_type", "")).strip()
    raw_source_uid = str(frontmatter.get("source_uid", "")).strip()
    digest_status = str(frontmatter.get("digest_status", "")).strip()
    if not raw_id or not ingested:
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_RAW",
            f"结构化 raw 缺少 raw_id 或 ingested: {path}",
        )
    _ingested_sort_key(ingested, path=path)
    if raw_source_type != source_type or raw_source_uid != source_uid:
        raise _error(
            "SOURCE_SNAPSHOT_SOURCE_MISMATCH",
            f"raw frontmatter 与请求的来源身份不一致: {path}",
            details={
                "expected_source_type": source_type,
                "expected_source_uid": source_uid,
                "actual_source_type": raw_source_type,
                "actual_source_uid": raw_source_uid,
            },
        )
    if digest_status and digest_status != "digested":
        raise _error(
            "SOURCE_SNAPSHOT_INCOMPLETE_RAW",
            f"结构化 raw 尚未完成 digest: {path}",
            details={"digest_status": digest_status},
        )

    snapshot = dict(_snapshot_candidates(body, path=path)[0])
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise _error(
            "SOURCE_SNAPSHOT_INCOMPLETE_RAW",
            f"snapshot 缺少 records 数组: {path}",
        )

    snapshot_type = str(snapshot.get("source_type", "")).strip()
    snapshot_uid = str(snapshot.get("source_uid", "")).strip()
    if snapshot_type and snapshot_type != source_type:
        raise _error(
            "SOURCE_SNAPSHOT_SOURCE_MISMATCH",
            f"snapshot source_type 与 raw frontmatter 不一致: {path}",
            details={"frontmatter": source_type, "snapshot": snapshot_type},
        )
    if snapshot_uid and snapshot_uid != source_uid:
        raise _error(
            "SOURCE_SNAPSHOT_SOURCE_MISMATCH",
            f"snapshot source_uid 与 raw frontmatter 不一致: {path}",
            details={"frontmatter": source_uid, "snapshot": snapshot_uid},
        )

    # Early structured raws sometimes omitted identity inside the JSON snapshot.
    # Preserve compatibility while making the value safe for diff_captures.
    identity_inferred = not snapshot_type or not snapshot_uid
    snapshot["source_type"] = source_type
    snapshot["source_uid"] = source_uid
    relative_path = str(path.resolve().relative_to(kb.resolve()))
    provenance = RawSnapshotProvenance(
        raw_id=raw_id,
        raw_path=relative_path,
        ingested=ingested,
        source_type=source_type,
        source_uid=source_uid,
        source_url=str(frontmatter.get("source_url", "")).strip(),
        source_title=str(frontmatter.get("source_title", "")).strip(),
        source_revision=str(frontmatter.get("source_revision", "")).strip(),
        raw_content_hash=str(frontmatter.get("content_hash", "")).strip(),
        digest_status=digest_status,
    )
    return PersistedSnapshot(
        snapshot=snapshot,
        provenance=provenance,
        snapshot_hash=_canonical_hash(snapshot),
        identity_inferred=identity_inferred,
    )


def list_snapshots(
    kb: Path,
    *,
    source_type: str,
    source_uid: str,
) -> tuple[PersistedSnapshot, ...]:
    """Return one source's validated history, newest first."""
    kb = kb.expanduser().resolve()
    raw_dir = kb / "raw_data"
    if not raw_dir.is_dir():
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_KB",
            f"知识库缺少 raw_data 目录: {kb}",
        )
    if not source_type.strip() or not source_uid.strip():
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_REQUEST",
            "source_type 与 source_uid 不能为空",
        )

    candidates: list[tuple[tuple[float, str], PersistedSnapshot]] = []
    seen_raw_ids: set[str] = set()
    for path in sorted(raw_dir.glob("*.md")):
        try:
            frontmatter, _ = parse_file(str(path))
        except (OSError, UnicodeError) as exc:
            raise _error(
                "SOURCE_SNAPSHOT_INVALID_RAW",
                f"无法读取 raw: {path}",
            ) from exc
        if (
            str(frontmatter.get("source_type", "")).strip() != source_type
            or str(frontmatter.get("source_uid", "")).strip() != source_uid
        ):
            continue
        persisted = _load_raw_snapshot(
            kb,
            path,
            source_type=source_type,
            source_uid=source_uid,
        )
        if persisted.provenance.raw_id in seen_raw_ids:
            raise _error(
                "SOURCE_SNAPSHOT_DUPLICATE_RAW_ID",
                f"同一来源存在重复 raw_id: {persisted.provenance.raw_id}",
            )
        seen_raw_ids.add(persisted.provenance.raw_id)
        candidates.append(
            (
                _ingested_sort_key(
                    persisted.provenance.ingested,
                    path=path,
                ),
                persisted,
            )
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return tuple(item[1] for item in candidates)


def load_snapshot(
    kb: Path,
    *,
    source_type: str,
    source_uid: str,
    raw_id: str | None = None,
    history_index: int = 0,
) -> PersistedSnapshot | None:
    """Load the latest or an explicitly selected persisted snapshot.

    ``history_index=0`` means latest, ``1`` means the immediately preceding
    raw, and so on.  ``raw_id`` is the stronger selector and is mutually
    exclusive with a non-zero history index.  An empty history returns
    ``None`` only for the implicit latest lookup; explicit selectors fail.
    """
    if history_index < 0:
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_REQUEST",
            "history_index 不能为负数",
        )
    if raw_id and history_index:
        raise _error(
            "SOURCE_SNAPSHOT_INVALID_REQUEST",
            "raw_id 与非零 history_index 不能同时使用",
        )
    if raw_id:
        kb = kb.expanduser().resolve()
        raw_dir = kb / "raw_data"
        if not raw_dir.is_dir():
            raise _error(
                "SOURCE_SNAPSHOT_INVALID_KB",
                f"知识库缺少 raw_data 目录: {kb}",
            )
        matches: list[Path] = []
        for path in sorted(raw_dir.glob("*.md")):
            try:
                frontmatter, _ = parse_file(str(path))
            except (OSError, UnicodeError) as exc:
                raise _error(
                    "SOURCE_SNAPSHOT_INVALID_RAW",
                    f"无法读取 raw: {path}",
                ) from exc
            if str(frontmatter.get("raw_id", "")).strip() == raw_id:
                matches.append(path)
        if not matches:
            raise _error(
                "SOURCE_SNAPSHOT_NOT_FOUND",
                f"找不到指定 raw_id={raw_id} 的来源快照",
            )
        if len(matches) > 1:
            raise _error(
                "SOURCE_SNAPSHOT_DUPLICATE_RAW_ID",
                f"知识库存在重复 raw_id: {raw_id}",
            )
        return _load_raw_snapshot(
            kb,
            matches[0],
            source_type=source_type,
            source_uid=source_uid,
        )

    history = list_snapshots(
        kb,
        source_type=source_type,
        source_uid=source_uid,
    )
    if not history:
        if history_index:
            raise _error(
                "SOURCE_SNAPSHOT_NOT_FOUND",
                f"找不到 history_index={history_index} 的来源快照",
            )
        return None
    if history_index >= len(history):
        raise _error(
            "SOURCE_SNAPSHOT_NOT_FOUND",
            f"找不到 history_index={history_index} 的来源快照",
            details={"available": len(history)},
        )
    return history[history_index]


def diff_current_against_kb(
    current_capture: Mapping[str, Any],
    kb: Path,
    *,
    source_uid: str | None = None,
    raw_id: str | None = None,
    history_index: int = 0,
) -> dict[str, Any]:
    """Diff a live capture against its persisted KB history.

    With no history, ``diff_captures`` naturally emits baseline changes.
    The result also identifies the exact raw selected as the comparison base.
    """
    snapshot = current_capture.get("snapshot")
    if not isinstance(snapshot, Mapping):
        raise _error(
            "SOURCE_DIFF_INVALID",
            "current capture 缺少 snapshot 对象",
        )
    source_type = str(snapshot.get("source_type", "")).strip()
    capture_uid = str(snapshot.get("source_uid", "")).strip()
    if not source_type or not capture_uid:
        raise _error(
            "SOURCE_DIFF_INVALID",
            "current snapshot 缺少 source_type 或 source_uid",
        )
    if source_uid is not None and source_uid.strip() != capture_uid:
        raise _error(
            "SOURCE_SNAPSHOT_SOURCE_MISMATCH",
            "显式 source_uid 与 current snapshot 不一致",
            details={
                "expected_source_uid": source_uid.strip(),
                "actual_source_uid": capture_uid,
            },
        )
    previous = load_snapshot(
        kb,
        source_type=source_type,
        source_uid=capture_uid,
        raw_id=raw_id,
        history_index=history_index,
    )
    result = diff_captures(
        current=current_capture,
        previous=previous.as_capture() if previous is not None else None,
    )
    return {
        **result,
        "previous_raw": (
            previous.provenance.as_dict() if previous is not None else None
        ),
    }
