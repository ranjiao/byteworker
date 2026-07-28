"""Run a bounded KB compatibility migration after a skill update."""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

from doctor import DoctorReport, apply_repairs, scan


AUTO_FIX_ACTIONS = {"index", "links", "autolink"}
GLOBAL_REPAIR_BLOCKERS = {
    "KB_NOT_FOUND",
    "KB_INSIDE_SKILL_REPO",
    "KB_GIT_MISSING",
    "KB_GIT_HAS_REMOTE",
    "LAYOUT_MISSING_DIRECTORY",
    "TRUTH_FILE_MISSING",
}
NODE_GRAPH_BLOCKERS = {
    "NODE_UNREADABLE",
    "NODE_NO_FRONTMATTER",
    "NODE_UNCLOSED_FRONTMATTER",
    "NODE_DUPLICATE_FRONTMATTER_KEY",
    "NODE_MISSING_ID",
    "NODE_DUPLICATE_ID",
    "NODE_UNKNOWN_DIRECTORY",
    "NODE_TYPE_PATH_MISMATCH",
    "NODE_INVALID_TYPE",
    "NODE_ID_TYPE_MISMATCH",
}
INDEX_BLOCKERS = NODE_GRAPH_BLOCKERS | {
    "RAW_UNREADABLE",
    "RAW_NO_FRONTMATTER",
    "RAW_UNCLOSED_FRONTMATTER",
    "RAW_DUPLICATE_FRONTMATTER_KEY",
    "RAW_DUPLICATE_ID",
    "INDEX_REBUILD_FAILED",
}


def _one_line(value: str, limit: int = 180) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


@dataclass
class PostflightResult:
    status: str
    repaired_findings: int = 0
    changed_files: int = 0
    commit: str = ""
    summary: Dict[str, int] = field(
        default_factory=lambda: {
            "error": 0,
            "warning": 0,
            "info": 0,
            "auto_fixable": 0,
        }
    )
    reasons: List[str] = field(default_factory=list)
    repairs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _git(kb: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(kb), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _git_paths(kb: Path, *args: str) -> Set[str]:
    result = _git(kb, *args, "-z")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "无法读取知识库 Git 状态")
    return {value for value in result.stdout.split("\0") if value}


def _dirty_paths(kb: Path) -> Set[str]:
    paths = _git_paths(kb, "diff", "--name-only")
    paths.update(_git_paths(kb, "diff", "--cached", "--name-only"))
    paths.update(
        _git_paths(kb, "ls-files", "--others", "--exclude-standard")
    )
    return paths


def _staged_paths(kb: Path) -> Set[str]:
    return _git_paths(kb, "diff", "--cached", "--name-only")


def _actions(report: DoctorReport) -> tuple[List[str], bool]:
    fixes = {item.auto_fix for item in report.findings if item.auto_fix}
    unknown = sorted(fixes - AUTO_FIX_ACTIONS)
    if unknown:
        raise RuntimeError("doctor 返回未知 auto_fix: " + ", ".join(unknown))
    actions: List[str] = []
    if "index" in fixes:
        actions.append("index")
    if fixes & {"links", "autolink"}:
        actions.append("links")
    return actions, "autolink" in fixes


def _repair_blockers(
    kb: Path,
    actions: List[str],
    *,
    journal_path: str,
    report: DoctorReport,
) -> List[str]:
    reasons: List[str] = []
    codes = {item.code for item in report.findings}
    if codes & GLOBAL_REPAIR_BLOCKERS:
        reasons.append("知识库布局或 Git 隔离存在严重错误")
    if "links" in actions and codes & NODE_GRAPH_BLOCKERS:
        reasons.append("节点图存在影响确定性修复的结构错误")
    if "index" in actions and codes & INDEX_BLOCKERS:
        reasons.append("节点或 raw 存在影响 INDEX 重建的结构错误")
    staged = _staged_paths(kb)
    if staged:
        reasons.append("知识库已有 staged 变更")
    dirty = _dirty_paths(kb)
    if "index" in actions and "INDEX.md" in dirty:
        reasons.append("INDEX.md 正在被编辑")
    if "links" in actions and any(
        path.startswith("knowledge/") and path.endswith(".md") for path in dirty
    ):
        reasons.append("knowledge 节点存在未提交编辑")
    if journal_path in dirty:
        reasons.append("当天 journal 存在未提交编辑")
    return reasons


def _append_journal(
    kb: Path,
    *,
    repaired_findings: int,
    changed_files: int,
    actions: List[str],
    summary: Dict[str, int],
    now: datetime,
) -> str:
    relative = f"journal/{now:%Y-%m}/{now:%Y-%m-%d}.md"
    path = kb / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text += "\n"
    else:
        text = f"# {now:%Y-%m-%d}\n\n"
    line = (
        f"- {now:%H:%M} skill 更新后 doctor 自动修复："
        f"actions={','.join(actions)}；修复 findings={repaired_findings}；"
        f"修改文件={changed_files}；复扫 error={summary['error']}、"
        f"warning={summary['warning']}、info={summary['info']}；"
        "仅处理确定性 auto_fix，无业务语义改写。\n"
    )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text + line, encoding="utf-8")
    temporary.replace(path)
    return relative


def _commit_repairs(kb: Path, paths: List[str]) -> str:
    add = _git(kb, "add", "--", *paths)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "doctor 修复暂存失败")
    check = _git(kb, "diff", "--cached", "--check")
    if check.returncode != 0:
        raise RuntimeError(check.stdout.strip() or check.stderr.strip())
    commit = _git(kb, "commit", "-m", "doctor: auto-repair after skill update")
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip())
    head = _git(kb, "rev-parse", "--short", "HEAD")
    if head.returncode != 0:
        raise RuntimeError(head.stderr.strip() or "无法读取 doctor 修复提交")
    return head.stdout.strip()


def run_postflight(
    skill_root: Path,
    kb: Path,
    *,
    now: datetime | None = None,
) -> PostflightResult:
    skill_root = skill_root.resolve()
    kb = kb.resolve()
    now = now or datetime.now().astimezone()
    initial = scan(kb, skill_root)
    actions, autolink = _actions(initial)
    repairable = {
        (item.code, item.path, item.auto_fix)
        for item in initial.findings
        if item.auto_fix
    }
    reasons: List[str] = []
    repairs: List[Dict[str, Any]] = []
    commit = ""
    changed_files = 0
    final = initial

    if actions:
        git_check = _git(kb, "rev-parse", "--is-inside-work-tree")
        if git_check.returncode != 0:
            reasons.append("知识库没有可用的本地 Git 回滚点")
        else:
            journal_path = f"journal/{now:%Y-%m}/{now:%Y-%m-%d}.md"
            reasons.extend(
                _repair_blockers(
                    kb,
                    actions,
                    journal_path=journal_path,
                    report=initial,
                )
            )
        if not reasons:
            before = _dirty_paths(kb)
            repairs = apply_repairs(
                kb,
                skill_root,
                actions,
                autolink=autolink,
            )
            failed = [item["action"] for item in repairs if not item["ok"]]
            if failed:
                reasons.append("自动修复执行失败: " + ", ".join(failed))
            final = scan(kb, skill_root)
            after = _dirty_paths(kb)
            repair_paths = sorted(after - before)
            allowed = [
                path
                for path in repair_paths
                if path == "INDEX.md"
                or (path.startswith("knowledge/") and path.endswith(".md"))
            ]
            unexpected = sorted(set(repair_paths) - set(allowed))
            if unexpected:
                reasons.append("自动修复触达未知路径")
            repaired = repairable - {
                (item.code, item.path, item.auto_fix)
                for item in final.findings
                if item.auto_fix
            }
            if allowed and not unexpected:
                try:
                    journal = _append_journal(
                        kb,
                        repaired_findings=len(repaired),
                        changed_files=len(allowed),
                        actions=actions,
                        summary=final.summary(),
                        now=now,
                    )
                    commit = _commit_repairs(kb, [*allowed, journal])
                    changed_files = len(allowed)
                except RuntimeError as exc:
                    reasons.append(str(exc))

    summary = final.summary()
    if summary["error"]:
        reasons.append(f"复扫仍有 {summary['error']} 项 error")
    if summary["auto_fixable"]:
        reasons.append(f"复扫仍有 {summary['auto_fixable']} 项 auto_fix")
    repaired_findings = len(repairable) - summary["auto_fixable"]
    if reasons:
        status = "decision"
    elif summary["warning"] or summary["info"]:
        status = "notice"
    else:
        status = "healthy"
    return PostflightResult(
        status=status,
        repaired_findings=max(repaired_findings, 0),
        changed_files=changed_files,
        commit=commit,
        summary=summary,
        reasons=list(dict.fromkeys(reasons)),
        repairs=repairs,
    )


def render_message(result: PostflightResult) -> str:
    summary = result.summary
    repaired = (
        f"已自动修复 {result.repaired_findings} 项"
        + (f"并创建本地回滚提交 {result.commit}" if result.commit else "")
        + "；"
        if result.repaired_findings
        else ""
    )
    if result.status == "decision":
        reason = "；".join(_one_line(value) for value in result.reasons[:3])
        return (
            f"doctor:{repaired}仍有无法自动处理的严重问题"
            f"(error={summary['error']},warning={summary['warning']},"
            f"info={summary['info']})，{reason}。请决定是否立即检查。"
        )
    if result.status == "notice":
        return (
            f"doctor:{repaired}检查后仍有 warning={summary['warning']}/"
            f"info={summary['info']}；可忽略，或回复“立即处理 doctor 问题”。"
        )
    return f"doctor:{repaired}兼容检查通过。"
