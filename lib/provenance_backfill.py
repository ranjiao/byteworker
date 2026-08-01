"""Plan and apply conservative provenance backfills for an existing KB."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from frontmatter import parse_file, parse_frontmatter
from kb_write_txn import kb_write_lock
from provenance import (
    BACKFILL_SCHEMA,
    EVIDENCE_MARKER_RE,
    ProvenanceError,
    anchor_index,
    build_provenance_document,
    extract_offline_anchors,
    list_value,
    materialize_node_provenance,
    provenance_path,
    render_provenance,
    scan_provenance,
    scan_raws,
    sha256_bytes,
    source_url_from_frontmatter,
)


class BackfillError(RuntimeError):
    """A safe provenance backfill error."""


@dataclass
class BackfillValidation:
    plan: Dict[str, Any]
    writes: Dict[Path, bytes]
    nodes: List[str]
    raws: List[str]
    warnings: List[str]


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _git(kb: Path, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        ["git", *args],
        cwd=kb,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise BackfillError(f"git {' '.join(args)} 失败: {message}")
    return completed


def _dirty_paths(kb: Path) -> set[str]:
    output = _git(kb, ["status", "--porcelain=v1", "-z"]).stdout
    result: set[str] = set()
    for item in output.split("\0"):
        if not item:
            continue
        path = item[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        result.add(path)
    return result


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _safe_kb_path(kb: Path, value: str, prefix: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise BackfillError(f"非法知识库相对路径: {value}")
    if not relative.parts or relative.parts[0] != prefix:
        raise BackfillError(f"目标路径必须位于 {prefix}/: {value}")
    target = (kb / relative).resolve()
    try:
        target.relative_to(kb.resolve())
    except ValueError as exc:
        raise BackfillError(f"目标路径逃逸知识库: {value}") from exc
    cursor = kb
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BackfillError(f"目标路径经过符号链接: {value}")
    return target


def audit_kb(kb: Path) -> Dict[str, Any]:
    kb = kb.resolve()
    try:
        raws = scan_raws(kb)
        provenance = scan_provenance(kb)
    except ProvenanceError as exc:
        raise BackfillError(str(exc)) from exc
    raw_counts: Dict[str, Dict[str, int]] = {}
    for raw_id, record in raws.items():
        frontmatter = record["frontmatter"]
        source_type = str(frontmatter.get("source_type") or "unknown")
        counts = raw_counts.setdefault(
            source_type,
            {
                "total": 0,
                "open_url": 0,
                "provenance": 0,
                "offline_exact_anchor": 0,
            },
        )
        counts["total"] += 1
        if source_url_from_frontmatter(frontmatter):
            counts["open_url"] += 1
        if raw_id in provenance:
            counts["provenance"] += 1
        offline = extract_offline_anchors(raw_id, frontmatter, record["body"])
        if any(item["precision"] == "exact" for item in offline):
            counts["offline_exact_anchor"] += 1

    node_counts = {
        "total": 0,
        "with_sources": 0,
        "with_primary_source": 0,
        "with_primary_source_url": 0,
        "with_inline_evidence": 0,
        "unambiguous_primary_candidate": 0,
        "ambiguous_primary_candidate": 0,
        "no_open_source_candidate": 0,
    }
    for path in sorted((kb / "knowledge").glob("**/*.md")):
        frontmatter, body = parse_file(str(path))
        node_counts["total"] += 1
        sources = list_value(frontmatter.get("sources"))
        if sources:
            node_counts["with_sources"] += 1
        if frontmatter.get("primary_source"):
            node_counts["with_primary_source"] += 1
        if frontmatter.get("primary_source_url"):
            node_counts["with_primary_source_url"] += 1
        if EVIDENCE_MARKER_RE.search(body):
            node_counts["with_inline_evidence"] += 1
        candidates = [
            raw_id
            for raw_id in sources
            if raw_id in raws
            and source_url_from_frontmatter(raws[raw_id]["frontmatter"])
        ]
        if len(candidates) == 1:
            node_counts["unambiguous_primary_candidate"] += 1
        elif len(candidates) > 1:
            node_counts["ambiguous_primary_candidate"] += 1
        else:
            node_counts["no_open_source_candidate"] += 1
    return {
        "status": "ok",
        "schema_version": BACKFILL_SCHEMA,
        "raws": raw_counts,
        "nodes": node_counts,
        "mode": "read-only",
    }


def build_plan(kb: Path, generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    kb = kb.resolve()
    generated_at = generated_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    try:
        raws = scan_raws(kb)
        existing_provenance = scan_provenance(kb)
    except ProvenanceError as exc:
        raise BackfillError(str(exc)) from exc
    raw_plans: List[Dict[str, Any]] = []
    for raw_id, record in sorted(raws.items()):
        if raw_id in existing_provenance:
            continue
        frontmatter = record["frontmatter"]
        raw_plans.append(
            {
                "raw_id": raw_id,
                "raw_path": record["relative_path"],
                "raw_sha256": record["file_sha256"],
                "provenance_path": str(
                    provenance_path(kb, raw_id).relative_to(kb)
                ),
                "apply": False,
                "enrichment": "offline",
                "anchors": extract_offline_anchors(
                    raw_id,
                    frontmatter,
                    record["body"],
                ),
            }
        )

    node_plans: List[Dict[str, Any]] = []
    for path in sorted((kb / "knowledge").glob("**/*.md")):
        frontmatter, _ = parse_file(str(path))
        sources = list_value(frontmatter.get("sources"))
        candidates = [
            raw_id
            for raw_id in sources
            if raw_id in raws
            and source_url_from_frontmatter(raws[raw_id]["frontmatter"])
        ]
        existing_primary = str(frontmatter.get("primary_source", "")).strip()
        if existing_primary:
            status = "existing"
            proposal = existing_primary
        elif len(candidates) == 1:
            status = "ready"
            proposal = candidates[0]
        elif len(candidates) > 1:
            status = "ambiguous"
            proposal = ""
        else:
            status = "unavailable"
            proposal = ""
        node_plans.append(
            {
                "id": str(frontmatter.get("id", "")),
                "path": str(path.relative_to(kb)),
                "base_sha256": sha256_file(path),
                "apply": False,
                "status": status,
                "primary_source": proposal,
                "evidence": [],
            }
        )
    return {
        "schema_version": BACKFILL_SCHEMA,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "raws": raw_plans,
        "nodes": node_plans,
        "commit": {"message": "backfill knowledge provenance"},
    }


def _load_plan(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackfillError(f"无法读取 backfill plan: {path}: {exc}") from exc
    if value.get("schema_version") != BACKFILL_SCHEMA:
        raise BackfillError(
            f"不支持的 backfill schema: {value.get('schema_version')!r}"
        )
    return value


def _resolve_candidate(path_value: str, plan_path: Path) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = plan_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise BackfillError(f"候选节点不存在: {path}")
    return path


def validate_backfill(kb: Path, plan_path: Path) -> BackfillValidation:
    kb = kb.resolve()
    plan_path = plan_path.resolve()
    plan = _load_plan(plan_path)
    try:
        raw_records = scan_raws(kb)
        existing_provenance = scan_provenance(kb)
    except ProvenanceError as exc:
        raise BackfillError(str(exc)) from exc
    planned_provenance: Dict[str, Dict[str, Any]] = {}
    writes: Dict[Path, bytes] = {}
    selected_raws: List[str] = []
    selected_nodes: List[str] = []
    warnings: List[str] = []
    generated_at = str(plan.get("generated_at", "")).strip() or datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).isoformat(timespec="seconds")

    raw_configs = plan.get("raws", [])
    if not isinstance(raw_configs, list):
        raise BackfillError("plan.raws 必须是数组")
    for config in raw_configs:
        if not isinstance(config, dict) or not config.get("apply"):
            continue
        raw_id = str(config.get("raw_id", "")).strip()
        record = raw_records.get(raw_id)
        if not record:
            raise BackfillError(f"backfill raw 不存在: {raw_id}")
        if str(config.get("raw_sha256", "")).strip() != record["file_sha256"]:
            raise BackfillError(f"raw 基线已变化: {raw_id}")
        target = _safe_kb_path(
            kb,
            str(config.get("provenance_path", "")),
            "provenance",
        )
        if target.exists() and not config.get("replace"):
            raise BackfillError(f"provenance 已存在，未声明 replace: {raw_id}")
        anchors = config.get("anchors", [])
        if not isinstance(anchors, list):
            raise BackfillError(f"{raw_id}.anchors 必须是数组")
        document = build_provenance_document(
            raw_id,
            record["relative_path"],
            record["frontmatter"],
            anchors,
            str(
                record["frontmatter"].get("content_hash")
                or record["file_sha256"]
            ),
            generated_at,
            str(config.get("enrichment") or "offline"),
        )
        planned_provenance[raw_id] = document
        writes[target] = render_provenance(document).encode("utf-8")
        selected_raws.append(raw_id)

    node_configs = plan.get("nodes", [])
    if not isinstance(node_configs, list):
        raise BackfillError("plan.nodes 必须是数组")
    for config in node_configs:
        if not isinstance(config, dict) or not config.get("apply"):
            continue
        target = _safe_kb_path(kb, str(config.get("path", "")), "knowledge")
        if not target.is_file():
            raise BackfillError(f"backfill 节点不存在: {target.relative_to(kb)}")
        if str(config.get("base_sha256", "")).strip() != sha256_file(target):
            raise BackfillError(f"节点基线已变化: {target.relative_to(kb)}")
        content = target.read_text(encoding="utf-8")
        candidate_value = str(config.get("candidate", "")).strip()
        if candidate_value:
            content = _resolve_candidate(
                candidate_value,
                plan_path,
            ).read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
        node_id = str(frontmatter.get("id", "")).strip()
        if node_id != str(config.get("id", "")).strip():
            raise BackfillError(f"节点 id 与 plan 不一致: {target.relative_to(kb)}")
        sources = set(list_value(frontmatter.get("sources")))
        primary_source = str(config.get("primary_source", "")).strip()
        primary_url = ""
        if primary_source:
            if primary_source not in sources:
                raise BackfillError(
                    f"{node_id} primary_source 不在 sources: {primary_source}"
                )
            record = raw_records.get(primary_source)
            if not record:
                raise BackfillError(f"{node_id} 找不到 primary raw: {primary_source}")
            primary_url = source_url_from_frontmatter(record["frontmatter"])
            if not primary_url:
                warnings.append(f"{node_id} 的主要来源没有可打开 URL")

        evidence_config = config.get("evidence")
        if evidence_config is None:
            evidence: List[Dict[str, Any]] = []
        elif not isinstance(evidence_config, list):
            raise BackfillError(f"{node_id}.evidence 必须是数组")
        else:
            evidence = []
            for item in evidence_config:
                if not isinstance(item, dict):
                    raise BackfillError(f"{node_id} evidence 项必须是对象")
                raw_id = str(item.get("raw_id", "")).strip()
                anchor_id = str(item.get("anchor_id", "")).strip()
                document = planned_provenance.get(raw_id) or existing_provenance.get(
                    raw_id
                )
                if not document and anchor_id == "source" and raw_id in raw_records:
                    offline = extract_offline_anchors(
                        raw_id,
                        raw_records[raw_id]["frontmatter"],
                        raw_records[raw_id]["body"],
                    )
                    document = {"anchors": offline}
                anchor = anchor_index(document).get(anchor_id) if document else None
                if not anchor:
                    raise BackfillError(
                        f"{node_id} 找不到 evidence anchor: {raw_id}#{anchor_id}"
                    )
                evidence.append(
                    {
                        "id": str(item.get("id", "")).strip(),
                        "raw_id": raw_id,
                        "anchor_id": anchor_id,
                        "anchor": anchor,
                    }
                )
        try:
            if primary_source or evidence_config is not None:
                content = materialize_node_provenance(
                    content,
                    str(target.relative_to(kb)),
                    primary_source,
                    primary_url,
                    evidence,
                    raw_records,
                )
        except ProvenanceError as exc:
            raise BackfillError(f"{node_id}: {exc}") from exc
        writes[target] = content.encode("utf-8")
        selected_nodes.append(node_id)

    if not writes:
        warnings.append("plan 没有 apply=true 的 raw 或 node")
    return BackfillValidation(
        plan=plan,
        writes=writes,
        nodes=selected_nodes,
        raws=selected_raws,
        warnings=warnings,
    )


def validation_report(result: BackfillValidation, kb: Path) -> Dict[str, Any]:
    return {
        "status": "valid",
        "writes": [str(path.relative_to(kb.resolve())) for path in result.writes],
        "nodes": result.nodes,
        "raws": result.raws,
        "warnings": result.warnings,
    }


def apply_backfill(
    kb: Path,
    plan_path: Path,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    kb = kb.resolve()
    if not (kb / ".git").is_dir():
        raise BackfillError("知识库不是本地 Git 仓库")
    with kb_write_lock(kb):
        result = validate_backfill(kb, plan_path)
        if not result.writes:
            return {
                "status": "noop",
                "nodes": [],
                "raws": [],
                "warnings": result.warnings,
            }
        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        journal_path = kb / "journal" / now.strftime("%Y-%m") / (
            now.strftime("%Y-%m-%d") + ".md"
        )
        writes = dict(result.writes)
        existing_journal = (
            journal_path.read_text(encoding="utf-8")
            if journal_path.exists()
            else f"# {now:%Y-%m-%d}\n"
        )
        if existing_journal and not existing_journal.endswith("\n"):
            existing_journal += "\n"
        existing_journal += (
            f"- {now:%H:%M} provenance backfill | "
            f"nodes={len(result.nodes)} | raws={len(result.raws)} | conflict=no\n"
        )
        writes[journal_path] = existing_journal.encode("utf-8")
        relative_paths = [str(path.relative_to(kb)) for path in writes]

        if _git(kb, ["remote"]).stdout.splitlines():
            raise BackfillError(
                "知识库 Git 配置了 remote，拒绝执行 provenance backfill"
            )
        staged = _git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines()
        if staged:
            raise BackfillError("知识库已有 staged 变更，拒绝混入 backfill commit")
        overlap = sorted(_dirty_paths(kb) & set(relative_paths))
        if overlap:
            raise BackfillError(
                "backfill 目标已有未提交修改: " + ", ".join(overlap)
            )

        snapshots = {
            path: path.read_bytes() if path.exists() else None for path in writes
        }
        git_index_path = kb / ".git" / "index"
        git_index_snapshot = (
            git_index_path.read_bytes() if git_index_path.exists() else None
        )
        staged_by_backfill = False
        try:
            for path, content in writes.items():
                _atomic_write(path, content)
            diff_check = _git(
                kb,
                ["diff", "--check", "--", *relative_paths],
                check=False,
            )
            if diff_check.returncode != 0:
                raise BackfillError(
                    "git diff --check 失败: "
                    + (diff_check.stdout.strip() or diff_check.stderr.strip())
                )
            _git(kb, ["add", "--", *relative_paths])
            staged_by_backfill = True
            message = str(
                result.plan.get("commit", {}).get("message")
                or "backfill knowledge provenance"
            ).strip()
            _git(kb, ["commit", "-m", message])
            commit_hash = _git(kb, ["rev-parse", "HEAD"]).stdout.strip()
        except Exception:
            if staged_by_backfill:
                if git_index_snapshot is None:
                    try:
                        git_index_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    _atomic_write(git_index_path, git_index_snapshot)
            for path, content in snapshots.items():
                if content is None:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    _atomic_write(path, content)
            raise
    return {
        "status": "committed",
        "nodes": result.nodes,
        "raws": result.raws,
        "journal": str(journal_path.relative_to(kb)),
        "commit": commit_hash,
        "warnings": result.warnings,
    }
