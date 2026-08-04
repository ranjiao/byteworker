"""Deterministic non-digest KB mutation plans and transactions."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from kb_write_txn import (
    atomic_write,
    kb_write_lock,
    restore_files,
    restore_git_index,
    snapshot_files,
    snapshot_git_index,
)


MUTATION_SCHEMA = "byteworker-kb-mutation/v1"
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
WRITE_MODES = {"replace", "replace_section", "replace_preserving_sections"}
WRITE_FIELDS = {
    "path",
    "mode",
    "content_path",
    "base_sha256",
    "section",
    "preserve_sections",
}
CONFLICT_DISPOSITIONS = {
    "no_conflict",
    "user_confirmed",
    "revision",
    "supersede",
}
CONTEXT_SECTIONS = {
    "我的身份",
    "我的职责范围",
    "我的当前重点",
    "主管方向",
    "当前约束",
    "交互与提醒偏好",
    "背景信息",
}
PRESERVABLE_SECTIONS = {
    "手动补充 / 备注",
    "长期关注",
    "需要关注",
}


class MutationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedWrite:
    path: Path
    relative_path: str
    content: bytes
    base_sha256: str
    mode: str


@dataclass(frozen=True)
class MutationValidation:
    plan: Mapping[str, Any]
    writes: tuple[PreparedWrite, ...]
    rebuild_index: bool


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _git(
    kb: Path,
    args: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=kb,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise MutationError(
            "KB_MUTATION_GIT_ERROR",
            completed.stderr.strip() or completed.stdout.strip() or "git 命令失败",
        )
    return completed


def _dirty_paths(kb: Path) -> set[str]:
    output = _git(kb, ["status", "--porcelain=v1", "-z"]).stdout
    chunks = output.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(chunks):
        entry = chunks[index]
        if not entry:
            index += 1
            continue
        status = entry[:2]
        value = entry[3:]
        if status.startswith(("R", "C")):
            index += 1
            if index < len(chunks):
                value = chunks[index]
        paths.add(value)
        index += 1
    return paths


def _load_plan(path: Path, skill_root: Path) -> Mapping[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved.is_relative_to(skill_root.resolve()):
        raise MutationError(
            "KB_MUTATION_PLAN_IN_SKILL_REPO",
            "mutation plan 含业务内容，不得放入 skill 仓库",
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            f"无法读取 mutation plan: {exc}",
        ) from exc
    if not isinstance(value, Mapping):
        raise MutationError("KB_MUTATION_PLAN_INVALID", "mutation plan 顶层必须是对象")
    return value


def _target(kb: Path, value: Any) -> tuple[Path, str]:
    relative = str(value or "").strip()
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise MutationError("KB_MUTATION_PATH_INVALID", f"非法目标路径: {relative}")
    unresolved = kb / candidate
    current = kb
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise MutationError(
                "KB_MUTATION_PATH_INVALID",
                f"目标路径不得经过符号链接: {relative}",
            )
    resolved = unresolved.resolve()
    if not resolved.is_relative_to(kb):
        raise MutationError("KB_MUTATION_PATH_INVALID", f"目标越出 KB: {relative}")
    if relative.startswith("reports/im/"):
        raise MutationError(
            "KB_MUTATION_PATH_FORBIDDEN",
            f"reports/im 是已移除 Inbox 的只读历史目录: {relative}",
        )
    allowed = (
        relative in {"context.md", "dashboard.md"}
        or (relative.startswith("knowledge/") and relative.endswith(".md"))
        or (relative.startswith("reports/") and relative.endswith(".md"))
    )
    if not allowed:
        raise MutationError(
            "KB_MUTATION_PATH_FORBIDDEN",
            f"mutation 只允许 context/dashboard/knowledge/reports: {relative}",
        )
    return resolved, relative


def _candidate_content(
    value: Any,
    *,
    skill_root: Path,
) -> bytes:
    path = Path(str(value or "")).expanduser().resolve()
    if path.is_relative_to(skill_root.resolve()):
        raise MutationError(
            "KB_MUTATION_CONTENT_IN_SKILL_REPO",
            "候选内容含业务数据，不得放入 skill 仓库",
        )
    try:
        content = path.read_bytes()
        content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MutationError(
            "KB_MUTATION_CONTENT_INVALID",
            f"候选内容必须是可读 UTF-8 文件: {exc}",
        ) from exc
    if len(content) > 2 * 1024 * 1024:
        raise MutationError(
            "KB_MUTATION_CONTENT_TOO_LARGE",
            "单个候选内容不得超过 2 MiB",
        )
    return content


def _section_span(text: str, heading: str) -> tuple[int, int]:
    match = re.search(rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*\r?$", text)
    if match is None:
        raise MutationError(
            "KB_MUTATION_SECTION_MISSING",
            f"目标缺少固定章节: {heading}",
        )
    following = re.search(r"(?m)^##[ \t]+.+$", text[match.end() :])
    end = match.end() + following.start() if following else len(text)
    return match.start(), end


def _replace_section(target: str, heading: str, body: str) -> str:
    if heading not in CONTEXT_SECTIONS | PRESERVABLE_SECTIONS:
        raise MutationError(
            "KB_MUTATION_SECTION_FORBIDDEN",
            f"不允许修改未登记章节: {heading}",
        )
    if re.search(r"(?m)^##[ \t]+", body):
        raise MutationError(
            "KB_MUTATION_SECTION_INVALID",
            "章节 body 不得包含二级标题",
        )
    start, end = _section_span(target, heading)
    heading_line = target[start : target.find("\n", start) + 1]
    if not heading_line:
        heading_line = f"## {heading}\n"
    replacement = heading_line + body.strip() + "\n\n"
    return target[:start] + replacement + target[end:].lstrip("\r\n")


def _preserve_sections(
    target: str,
    candidate: str,
    headings: Sequence[str],
) -> str:
    rendered = candidate
    for heading in headings:
        if heading not in PRESERVABLE_SECTIONS:
            raise MutationError(
                "KB_MUTATION_SECTION_FORBIDDEN",
                f"不允许声明保留章节: {heading}",
            )
        old_start, old_end = _section_span(target, heading)
        new_start, new_end = _section_span(rendered, heading)
        rendered = (
            rendered[:new_start]
            + target[old_start:old_end]
            + rendered[new_end:]
        )
    return rendered


def _prepare_write(
    kb: Path,
    raw: Mapping[str, Any],
    *,
    skill_root: Path,
) -> PreparedWrite:
    unknown = sorted(set(raw) - WRITE_FIELDS)
    if unknown:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            "write 含未知字段: " + ", ".join(unknown),
        )
    target, relative = _target(kb, raw.get("path"))
    mode = str(raw.get("mode", "replace")).strip()
    if mode not in WRITE_MODES:
        raise MutationError("KB_MUTATION_PLAN_INVALID", f"未知 write mode: {mode}")
    if mode == "replace" and (
        "section" in raw or "preserve_sections" in raw
    ):
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            "replace 不接受 section/preserve_sections",
        )
    if mode == "replace_section" and "preserve_sections" in raw:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            "replace_section 不接受 preserve_sections",
        )
    if mode == "replace_preserving_sections" and "section" in raw:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            "replace_preserving_sections 不接受 section",
        )
    base = str(raw.get("base_sha256", "")).strip()
    current = target.read_bytes() if target.is_file() else None
    if current is None:
        if base:
            raise MutationError(
                "KB_MUTATION_BASE_MISMATCH",
                f"新建目标的 base_sha256 必须为空: {relative}",
            )
    elif not SHA_RE.fullmatch(base) or sha256_bytes(current) != base:
        raise MutationError(
            "KB_MUTATION_BASE_MISMATCH",
            f"目标在分析后已变化: {relative}",
        )
    candidate = _candidate_content(raw.get("content_path"), skill_root=skill_root)
    if mode == "replace":
        content = candidate
    elif current is None:
        raise MutationError(
            "KB_MUTATION_TARGET_MISSING",
            f"{mode} 要求目标已存在: {relative}",
        )
    elif mode == "replace_section":
        heading = str(raw.get("section", "")).strip()
        content = _replace_section(
            current.decode("utf-8"),
            heading,
            candidate.decode("utf-8"),
        ).encode("utf-8")
    else:
        headings = raw.get("preserve_sections")
        if not isinstance(headings, list) or not headings:
            raise MutationError(
                "KB_MUTATION_PLAN_INVALID",
                "replace_preserving_sections 要求非空 preserve_sections",
            )
        content = _preserve_sections(
            current.decode("utf-8"),
            candidate.decode("utf-8"),
            [str(item).strip() for item in headings],
        ).encode("utf-8")
    if relative == "context.md" and len(content) > 32 * 1024:
        raise MutationError(
            "KB_MUTATION_CONTEXT_TOO_LARGE",
            "context.md 不得超过 32 KiB；请先归档过期条目",
        )
    return PreparedWrite(target, relative, content, base, mode)


def validate_mutation(
    kb: Path,
    plan_path: Path,
    skill_root: Path,
) -> MutationValidation:
    kb = kb.expanduser().resolve()
    skill_root = skill_root.resolve()
    plan = _load_plan(plan_path, skill_root)
    allowed_top = {
        "schema_version",
        "operation",
        "conflict_disposition",
        "conflict_evidence",
        "writes",
        "journal",
        "commit",
    }
    unknown = sorted(set(plan) - allowed_top)
    if unknown:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            "mutation plan 含未知字段: " + ", ".join(unknown),
        )
    if plan.get("schema_version") != MUTATION_SCHEMA:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            f"schema_version 必须是 {MUTATION_SCHEMA}",
        )
    operation = str(plan.get("operation", "")).strip()
    if operation not in {"update", "context", "dashboard", "report"}:
        raise MutationError("KB_MUTATION_PLAN_INVALID", "operation 非法")
    raw_writes = plan.get("writes")
    if not isinstance(raw_writes, list) or not raw_writes:
        raise MutationError("KB_MUTATION_PLAN_INVALID", "writes 必须是非空数组")
    prepared: list[PreparedWrite] = []
    for item in raw_writes:
        if not isinstance(item, Mapping):
            raise MutationError("KB_MUTATION_PLAN_INVALID", "write 必须是对象")
        prepared.append(_prepare_write(kb, item, skill_root=skill_root))
    writes = tuple(prepared)
    relatives = [item.relative_path for item in writes]
    if len(set(relatives)) != len(relatives):
        raise MutationError("KB_MUTATION_PLAN_INVALID", "writes 目标路径不得重复")
    target_matches_operation = {
        "context": lambda path: path == "context.md",
        "dashboard": lambda path: path == "dashboard.md",
        "report": lambda path: path.startswith("reports/"),
        "update": lambda path: path.startswith("knowledge/"),
    }[operation]
    mismatched = [path for path in relatives if not target_matches_operation(path)]
    if mismatched:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            f"{operation} operation 不允许目标: " + ", ".join(mismatched),
        )
    if any(path.startswith("knowledge/") for path in relatives):
        disposition = str(plan.get("conflict_disposition", "")).strip()
        if disposition not in CONFLICT_DISPOSITIONS:
            raise MutationError(
                "KB_MUTATION_CONFLICT_UNDECLARED",
                "knowledge mutation 必须声明 conflict_disposition",
            )
        if disposition != "no_conflict":
            evidence = plan.get("conflict_evidence")
            if not isinstance(evidence, list) or not any(
                str(item).strip() for item in evidence
            ):
                raise MutationError(
                    "KB_MUTATION_CONFLICT_EVIDENCE_MISSING",
                    "非 no_conflict 的 knowledge mutation 必须提供 conflict_evidence",
                )
    journal = plan.get("journal")
    if not isinstance(journal, Mapping):
        raise MutationError("KB_MUTATION_PLAN_INVALID", "journal 必须是对象")
    for field in ("action", "summary"):
        value = str(journal.get(field, "")).strip()
        if not value or "\n" in value or len(value) > 500:
            raise MutationError(
                "KB_MUTATION_PLAN_INVALID",
                f"journal.{field} 必须是有限单行文本",
            )
    commit = plan.get("commit")
    message = (
        str(commit.get("message", "")).strip()
        if isinstance(commit, Mapping)
        else ""
    )
    if not message or "\n" in message or len(message) > 200:
        raise MutationError(
            "KB_MUTATION_PLAN_INVALID",
            "commit.message 必须是有限单行文本",
        )
    return MutationValidation(
        plan=plan,
        writes=writes,
        rebuild_index=any(path.startswith("knowledge/") for path in relatives),
    )


def validation_report(result: MutationValidation) -> dict[str, Any]:
    return {
        "status": "valid",
        "operation": result.plan["operation"],
        "writes": [
            {
                "path": item.relative_path,
                "mode": item.mode,
                "content_sha256": sha256_bytes(item.content),
            }
            for item in result.writes
        ],
        "index_rebuild": result.rebuild_index,
    }


def _journal_content(
    existing: bytes | None,
    *,
    now: datetime,
    action: str,
    summary: str,
    targets: Sequence[str],
) -> bytes:
    text = (
        existing.decode("utf-8")
        if existing is not None
        else f"# {now:%Y-%m-%d}\n\n"
    )
    if text and not text.endswith("\n"):
        text += "\n"
    line = (
        f"- {now:%H:%M} {action} | targets={','.join(targets)} | "
        f"{summary}\n"
    )
    return (text + line).encode("utf-8")


def _head(kb: Path) -> str:
    return _git(kb, ["rev-parse", "HEAD"]).stdout.strip()


def _restore_head(kb: Path, head: str) -> None:
    current = _head(kb)
    if current == head:
        return
    moved = _git(
        kb,
        ["update-ref", "HEAD", head, current],
        check=False,
    )
    if moved.returncode != 0:
        raise MutationError(
            "KB_MUTATION_ROLLBACK_ERROR",
            moved.stderr.strip() or "无法回滚 mutation commit 引用",
        )


def execute_mutation(
    kb: Path,
    plan_path: Path,
    skill_root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    kb = kb.expanduser().resolve()
    skill_root = skill_root.resolve()
    if not (kb / ".git").is_dir():
        raise MutationError("KB_MUTATION_KB_INVALID", "知识库不是本地 Git 仓库")
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    with kb_write_lock(kb):
        result = validate_mutation(kb, plan_path, skill_root)
        remotes = _git(kb, ["remote"]).stdout.splitlines()
        if remotes:
            raise MutationError(
                "KB_MUTATION_REMOTE_FORBIDDEN",
                "知识库 Git 配置了 remote，拒绝写入",
            )
        staged = _git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines()
        if staged:
            raise MutationError(
                "KB_MUTATION_GIT_DIRTY",
                "知识库已有 staged 变更，拒绝混入 mutation commit",
            )
        journal_path = (
            kb
            / "journal"
            / now.strftime("%Y-%m")
            / f"{now:%Y-%m-%d}.md"
        )
        target_paths = [item.path for item in result.writes]
        if result.rebuild_index:
            target_paths.append(kb / "INDEX.md")
        target_paths.append(journal_path)
        relative_paths = [str(path.relative_to(kb)) for path in target_paths]
        overlap = sorted(_dirty_paths(kb) & set(relative_paths))
        if overlap:
            raise MutationError(
                "KB_MUTATION_GIT_DIRTY",
                "mutation 目标已有未提交修改: " + ", ".join(overlap),
            )
        snapshots = snapshot_files(target_paths)
        git_index = snapshot_git_index(kb)
        head = _head(kb)
        staged_by_txn = False
        try:
            for item in result.writes:
                atomic_write(item.path, item.content)
            if result.rebuild_index:
                rebuilt = subprocess.run(
                    [
                        sys.executable,
                        str(skill_root / "bin" / "rebuild_index.py"),
                        str(kb),
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if rebuilt.returncode != 0:
                    raise MutationError(
                        "KB_MUTATION_INDEX_ERROR",
                        rebuilt.stderr.strip()
                        or rebuilt.stdout.strip()
                        or "INDEX 重建失败",
                    )
            journal = result.plan["journal"]
            atomic_write(
                journal_path,
                _journal_content(
                    snapshots[journal_path],
                    now=now,
                    action=str(journal["action"]).strip(),
                    summary=str(journal["summary"]).strip(),
                    targets=[item.relative_path for item in result.writes],
                ),
            )
            diff_check = _git(
                kb,
                ["diff", "--check", "--", *relative_paths],
                check=False,
            )
            if diff_check.returncode != 0:
                raise MutationError(
                    "KB_MUTATION_DIFF_ERROR",
                    diff_check.stdout.strip() or diff_check.stderr.strip(),
                )
            _git(kb, ["add", "--", *relative_paths])
            staged_by_txn = True
            staged_after = set(
                _git(kb, ["diff", "--cached", "--name-only"]).stdout.splitlines()
            )
            unexpected = staged_after - set(relative_paths)
            if unexpected or not staged_after:
                raise MutationError(
                    "KB_MUTATION_STAGE_MISMATCH",
                    "mutation 暂存路径越界或没有产生变更",
                )
            _git(
                kb,
                ["commit", "-m", str(result.plan["commit"]["message"]).strip()],
            )
            commit = _git(kb, ["rev-parse", "HEAD"]).stdout.strip()
        except Exception as exc:
            rollback_error: Exception | None = None
            try:
                _restore_head(kb, head)
            except Exception as restore_exc:
                rollback_error = restore_exc
            finally:
                if staged_by_txn:
                    restore_git_index(kb, git_index)
                restore_files(snapshots)
            if rollback_error is not None:
                raise MutationError(
                    "KB_MUTATION_ROLLBACK_ERROR",
                    f"{exc}; 回滚 Git HEAD 失败: {rollback_error}",
                ) from exc
            raise
    return {
        "status": "committed",
        "operation": result.plan["operation"],
        "paths": [item.relative_path for item in result.writes],
        "journal": str(journal_path.relative_to(kb)),
        "index_rebuilt": result.rebuild_index,
        "commit": commit,
    }
