"""Todo Markdown persistence and exact-path Git transaction boundary."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from kb_write_txn import (
    atomic_write as atomic_write_bytes,
    kb_write_lock,
    restore_files,
    restore_git_index,
    snapshot_files,
    snapshot_git_index,
)
from todo_models import DEFAULT_PREAMBLE, FIELD_ORDER, VALID_KIND, VALID_STATUS, Todo


HEADING_RE = re.compile(r"^### \[([ xX])\] (T-\d{8}-\d{3}) · (.+)$")
FIELD_RE = re.compile(r"^- ([a-z_]+):\s*(.*)$")


class TodoTransactionError(RuntimeError):
    pass


def load_todos(path: Path) -> tuple[str, list[Todo]]:
    if not path.exists():
        return DEFAULT_PREAMBLE, []
    lines = path.read_text(encoding="utf-8").splitlines()
    active_index = next((i for i, line in enumerate(lines) if line == "## Active"), len(lines))
    if active_index == len(lines) or "## Completed" not in lines:
        raise ValueError("todo.md 缺少固定的 Active / Completed 章节，拒绝重写")
    preamble = "\n".join(lines[:active_index]).rstrip() + "\n"
    todos: list[Todo] = []
    current: Todo | None = None
    in_comment = False
    for line in lines[active_index:]:
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        heading = HEADING_RE.match(line)
        if heading:
            current = Todo(heading.group(2), heading.group(3).strip(), {})
            todos.append(current)
            continue
        if current:
            field_match = FIELD_RE.match(line)
            if field_match:
                current.fields[field_match.group(1)] = field_match.group(2).strip()
    ids = [todo.todo_id for todo in todos]
    if len(ids) != len(set(ids)):
        raise ValueError("todo.md 中存在重复 id，拒绝重写")
    for todo in todos:
        if todo.status not in VALID_STATUS:
            raise ValueError(f"{todo.todo_id} 的 status 非法：{todo.status}")
        kind = todo.fields.get("kind", "task")
        if kind not in VALID_KIND:
            raise ValueError(f"{todo.todo_id} 的 kind 非法：{kind}")
    return preamble, todos


def next_sort_time(todo: Todo) -> str:
    values = [todo.fields.get(key, "") for key in ("snoozed_until", "remind_at", "due_at")]
    return min((value for value in values if value), default="9999")


def render_todos(preamble: str, todos: Iterable[Todo]) -> str:
    todos_list = list(todos)
    active = sorted(
        (todo for todo in todos_list if todo.status in {"open", "waiting"}),
        key=next_sort_time,
    )
    completed = sorted(
        (todo for todo in todos_list if todo.status in {"done", "cancelled"}),
        key=lambda todo: todo.fields.get("updated_at", ""),
        reverse=True,
    )

    def render_item(todo: Todo) -> list[str]:
        checkbox = "x" if todo.status in {"done", "cancelled"} else " "
        result = [f"### [{checkbox}] {todo.todo_id} · {todo.title}"]
        seen = set()
        for key in FIELD_ORDER:
            value = todo.fields.get(key, "")
            result.append(f"- {key}: {value}" if value else f"- {key}:")
            seen.add(key)
        for key in sorted(set(todo.fields) - seen):
            result.append(f"- {key}: {todo.fields[key]}")
        return result

    output = [preamble.rstrip(), "", "## Active", ""]
    for todo in active:
        output.extend(render_item(todo))
        output.append("")
    output.extend(["## Completed", ""])
    for todo in completed:
        output.extend(render_item(todo))
        output.append("")
    return "\n".join(output).rstrip() + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def ensure_initialized(kb_dir: Path, template: Path | None) -> Path:
    path = kb_dir / "todo.md"
    if not path.exists():
        content = template.read_text(encoding="utf-8") if template else render_todos(DEFAULT_PREAMBLE, [])
        atomic_write(path, content)
    return path


def next_id(todos: Iterable[Todo], now: datetime) -> str:
    prefix = f"T-{now:%Y%m%d}-"
    numbers = [int(todo.todo_id.rsplit("-", 1)[1]) for todo in todos if todo.todo_id.startswith(prefix)]
    next_number = max(numbers, default=0) + 1
    if next_number > 999:
        raise ValueError("当天 Todo 已达到 999 条，无法继续分配 id")
    return f"{prefix}{next_number:03d}"


def get_todo(todos: Iterable[Todo], todo_id: str) -> Todo:
    for todo in todos:
        if todo.todo_id == todo_id:
            return todo
    raise ValueError(f"未找到 todo：{todo_id}")


def todo_json(todo: Todo) -> dict[str, str]:
    return {"id": todo.todo_id, "title": todo.title, **todo.fields}


def save(path: Path, preamble: str, todos: list[Todo]) -> None:
    atomic_write(path, render_todos(preamble, todos))


def _git(kb_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=kb_dir, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode != 0:
        raise TodoTransactionError(result.stderr.strip() or result.stdout.strip() or "git 命令失败")
    return result


def _git_paths(kb_dir: Path, *args: str) -> set[str]:
    result = _git(kb_dir, *args, "-z")
    return {item for item in result.stdout.split("\0") if item}


def _head(kb_dir: Path) -> str:
    return _git(kb_dir, "rev-parse", "HEAD").stdout.strip()


def _restore_head(kb_dir: Path, head: str) -> None:
    current = _head(kb_dir)
    if current != head:
        _git(kb_dir, "update-ref", "HEAD", head, current)


def _append_journal(existing: bytes | None, *, now: datetime, command: str, todo_id: str) -> bytes:
    text = existing.decode("utf-8") if existing is not None else f"# {now:%Y-%m-%d}\n\n"
    if text and not text.endswith("\n"):
        text += "\n"
    target = todo_id or "todo.md"
    return (text + f"- {now:%H:%M} Todo {command} | target={target}\n").encode("utf-8")


def execute_write_transaction(
    kb_dir: Path,
    *,
    now: datetime,
    command: str,
    operation: Callable[[], object],
) -> object:
    if not (kb_dir / ".git").is_dir():
        raise TodoTransactionError("知识库不是本地 Git 仓库")
    with kb_write_lock(kb_dir):
        if _git(kb_dir, "remote").stdout.splitlines():
            raise TodoTransactionError("知识库 Git 配置了 remote，拒绝写入")
        if _git_paths(kb_dir, "diff", "--cached", "--name-only"):
            raise TodoTransactionError("知识库已有 staged 变更，拒绝混入 Todo commit")
        todo_path = kb_dir / "todo.md"
        journal_path = kb_dir / "journal" / now.strftime("%Y-%m") / f"{now:%Y-%m-%d}.md"
        relative_paths = {"todo.md", str(journal_path.relative_to(kb_dir))}
        dirty = _git_paths(kb_dir, "diff", "--name-only")
        dirty.update(_git_paths(kb_dir, "ls-files", "--others", "--exclude-standard"))
        overlap = sorted(dirty & relative_paths)
        if overlap:
            raise TodoTransactionError("Todo 事务目标已有未提交修改: " + ", ".join(overlap))
        snapshots = snapshot_files([todo_path, journal_path])
        git_index = snapshot_git_index(kb_dir)
        head = _head(kb_dir)
        try:
            result = operation()
            current = todo_path.read_bytes() if todo_path.is_file() else None
            if current == snapshots[todo_path]:
                return result
            todo_id = str(result.get("id", "")) if isinstance(result, dict) else ""
            atomic_write_bytes(
                journal_path,
                _append_journal(snapshots[journal_path], now=now, command=command, todo_id=todo_id),
            )
            diff_check = _git(kb_dir, "diff", "--check", "--", *sorted(relative_paths), check=False)
            if diff_check.returncode != 0:
                raise TodoTransactionError(diff_check.stdout.strip() or diff_check.stderr.strip())
            _git(kb_dir, "add", "--", *sorted(relative_paths))
            staged = _git_paths(kb_dir, "diff", "--cached", "--name-only")
            if staged != relative_paths:
                raise TodoTransactionError("Todo 事务暂存路径不精确")
            _git(kb_dir, "commit", "-m", f"todo: {command}")
            commit = _head(kb_dir)
        except Exception as exc:
            rollback_error = None
            try:
                _restore_head(kb_dir, head)
                restore_git_index(kb_dir, git_index)
                restore_files(snapshots)
            except Exception as restore_exc:
                rollback_error = restore_exc
            if rollback_error is not None:
                raise TodoTransactionError(f"{exc}; 严重: Todo 回滚失败: {rollback_error}") from exc
            raise
        if isinstance(result, dict):
            result = {**result, "transaction": {"status": "committed", "commit": commit}}
        return result
