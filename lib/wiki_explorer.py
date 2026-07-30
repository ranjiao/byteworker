"""On-demand Feishu Wiki tree discovery and bounded candidate selection.

This module is intentionally not imported by Byteworker's normal digest/query
paths.  A Wiki space can contain thousands of nodes, so its regenerable tree
state lives outside the knowledge graph and is only touched by ``bin/wiki.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit


TREE_SCHEMA = "byteworker-wiki-tree-state/v1"
SUPPORTED_DOCUMENT_TYPES = {"doc", "docx"}
CHANGE_DETECTION_MODES = {"structure_only", "new_pages", "new_and_updated"}


class WikiError(RuntimeError):
    """Safe, stable error returned by the Wiki application service."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "message": str(self),
        }
        if self.hint:
            value["hint"] = self.hint
        if self.details:
            value["details"] = self.details
        return value


class WikiClient(Protocol):
    def auth_status(self) -> dict[str, Any]: ...

    def node_get(self, node_token_or_url: str) -> dict[str, Any]: ...

    def node_list(
        self,
        space_id: str,
        parent_node_token: str = "",
    ) -> list[dict[str, Any]]: ...


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(value: str, limit: int = 1500) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _error_from_process(
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> WikiError:
    combined = f"{stdout}\n{stderr}".lower()
    parsed: Any = None
    for candidate in (stderr, stdout):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            break
    message = ""
    if isinstance(parsed, Mapping):
        raw_error = parsed.get("error")
        if isinstance(raw_error, Mapping):
            message = str(raw_error.get("message", "")).strip()
        elif isinstance(raw_error, str):
            message = raw_error.strip()
        message = message or str(parsed.get("message", "")).strip()
    message = message or _bounded(stderr or stdout or "lark-cli command failed")

    if "keychain" in combined and (
        "blocked" in combined
        or "denied" in combined
        or "interaction is not allowed" in combined
    ):
        return WikiError(
            "WIKI_KEYCHAIN_ACCESS_BLOCKED",
            "当前进程无法读取 lark-cli 的用户凭据。",
            hint=(
                "请在 Terminal.app 或 iTerm 中执行 "
                "`security unlock-keychain \"$HOME/Library/Keychains/"
                "login.keychain-db\"`；若仍受限，再执行 "
                "`lark-cli config keychain-downgrade`，然后重试。"
            ),
        )
    if any(
        marker in combined
        for marker in (
            "not logged in",
            "login required",
            "user identity: unavailable",
            "token expired",
            "unauthorized",
        )
    ):
        return WikiError(
            "WIKI_AUTH_REQUIRED",
            "lark-cli 用户身份尚未完成授权或授权已失效。",
            hint="请在交互式终端完成 lark-cli 用户授权后重试。",
        )
    if any(
        marker in combined
        for marker in (
            "permission denied",
            "forbidden",
            "no permission",
            "access denied",
            "99991672",
        )
    ):
        return WikiError(
            "WIKI_PERMISSION_DENIED",
            "当前用户或应用没有读取该知识库节点的权限。",
            hint="确认用户能访问该知识库，并给应用开通 wiki:node:read 等读取权限。",
        )
    if any(marker in combined for marker in ("rate limit", "too many requests", "429")):
        return WikiError(
            "WIKI_RATE_LIMIT",
            "飞书 Wiki API 触发限流。",
            hint="稍后重试，或缩小到一个子树后再扫描。",
        )
    return WikiError(
        "WIKI_CLI_ERROR",
        _bounded(message),
        details={"exit_code": returncode},
    )


class LarkWikiClient:
    """Small argv-only adapter around lark-cli's user-identity Wiki commands."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.binary = binary or os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli")
        self.timeout_seconds = timeout_seconds

    def _run_json(self, args: Sequence[str]) -> dict[str, Any]:
        env = os.environ.copy()
        env.update(
            {
                "LARK_CLI_DISABLE_UPDATE_CHECK": "1",
                "LARK_CLI_DISABLE_NOTIFIER": "1",
                "NO_UPDATE_NOTIFIER": "1",
            }
        )
        try:
            completed = subprocess.run(
                [self.binary, *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise WikiError(
                "WIKI_CLI_NOT_FOUND",
                "找不到 lark-cli。",
                hint="请先安装并配置 lark-cli，或设置 BYTEWORKER_LARK_CLI_BIN。",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WikiError(
                "WIKI_CLI_TIMEOUT",
                "lark-cli Wiki 请求超时。",
                hint="缩小扫描子树或稍后重试。",
            ) from exc
        if completed.returncode:
            raise _error_from_process(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WikiError(
                "WIKI_CLI_PROTOCOL_ERROR",
                "lark-cli 未返回合法 JSON。",
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise _error_from_process(
                returncode=1,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise WikiError(
                "WIKI_CLI_PROTOCOL_ERROR",
                "lark-cli Wiki 响应缺少 data 对象。",
            )
        return dict(data)

    def auth_status(self) -> dict[str, Any]:
        env = os.environ.copy()
        env.update(
            {
                "LARK_CLI_DISABLE_UPDATE_CHECK": "1",
                "LARK_CLI_DISABLE_NOTIFIER": "1",
                "NO_UPDATE_NOTIFIER": "1",
            }
        )
        try:
            completed = subprocess.run(
                [self.binary, "auth", "status", "--json", "--verify"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise WikiError(
                "WIKI_CLI_NOT_FOUND",
                "找不到 lark-cli。",
                hint="请先安装并配置 lark-cli。",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WikiError(
                "WIKI_CLI_TIMEOUT",
                "lark-cli 授权状态检查超时。",
                hint="稍后重试。",
            ) from exc
        if completed.returncode:
            raise _error_from_process(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise WikiError(
                "WIKI_CLI_PROTOCOL_ERROR",
                "lark-cli auth status 未返回合法 JSON。",
            ) from exc
        identities = payload.get("identities", {}) if isinstance(payload, Mapping) else {}
        user = identities.get("user", {}) if isinstance(identities, Mapping) else {}
        user_message = str(user.get("message", ""))
        lowered_message = user_message.lower()
        if "keychain" in lowered_message and any(
            marker in lowered_message
            for marker in ("blocked", "denied", "interaction is not allowed")
        ):
            raise WikiError(
                "WIKI_KEYCHAIN_ACCESS_BLOCKED",
                "当前进程无法读取 lark-cli 的用户凭据。",
                hint=(
                    "请在 Terminal.app 或 iTerm 中执行 "
                    "`security unlock-keychain \"$HOME/Library/Keychains/"
                    "login.keychain-db\"`；若仍受限，再执行 "
                    "`lark-cli config keychain-downgrade`。"
                ),
            )
        scope = str(user.get("scope", ""))
        ready = bool(
            user.get("available")
            and user.get("verified")
            and user.get("status") == "ready"
            and user.get("tokenStatus") in ("valid", None)
        )
        wiki_scope = "wiki:node:read" in scope
        return {
            "ready": ready and wiki_scope,
            "identity": "user",
            "verified": bool(user.get("verified")),
            "token_status": user.get("tokenStatus"),
            "wiki_node_read_scope": wiki_scope,
            "user_name": str(user.get("userName", "")).strip(),
            "reason": "" if ready and wiki_scope else _bounded(user_message),
        }

    def node_get(self, node_token_or_url: str) -> dict[str, Any]:
        return self._run_json(
            [
                "wiki",
                "+node-get",
                "--node-token",
                node_token_or_url,
                "--as",
                "user",
                "--json",
            ]
        )

    def node_list(
        self,
        space_id: str,
        parent_node_token: str = "",
    ) -> list[dict[str, Any]]:
        args = [
            "wiki",
            "+node-list",
            "--space-id",
            space_id,
            "--page-size",
            "50",
            "--page-all",
            "--page-limit",
            "0",
            "--as",
            "user",
            "--json",
        ]
        if parent_node_token:
            args[4:4] = ["--parent-node-token", parent_node_token]
        data = self._run_json(args)
        nodes = data.get("nodes", [])
        if not isinstance(nodes, list) or not all(isinstance(item, Mapping) for item in nodes):
            raise WikiError(
                "WIKI_CLI_PROTOCOL_ERROR",
                "lark-cli Wiki node-list 响应缺少 nodes 数组。",
            )
        return [dict(item) for item in nodes]


def _node_record(
    value: Mapping[str, Any],
    *,
    depth: int,
    path_titles: Sequence[str],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "node_token": str(value.get("node_token", "")).strip(),
        "obj_token": str(value.get("obj_token", "")).strip(),
        "obj_type": str(value.get("obj_type", "")).strip(),
        "title": str(value.get("title", "")).strip(),
        "parent_node_token": str(value.get("parent_node_token", "")).strip(),
        "node_type": str(value.get("node_type", "")).strip(),
        "has_child": bool(value.get("has_child")),
        "depth": depth,
        "path_titles": list(path_titles),
    }
    for field in (
        "node_create_time",
        "obj_create_time",
        "obj_edit_time",
        "updated_at",
    ):
        if value.get(field) not in (None, ""):
            record[field] = str(value[field])
    return record


def _tree_hash(nodes: Iterable[Mapping[str, Any]]) -> str:
    structural = [
        {
            "node_token": item["node_token"],
            "obj_token": item.get("obj_token", ""),
            "obj_type": item.get("obj_type", ""),
            "title": item.get("title", ""),
            "parent_node_token": item.get("parent_node_token", ""),
            "has_child": bool(item.get("has_child")),
        }
        for item in nodes
    ]
    structural.sort(key=lambda item: item["node_token"])
    return "sha256:" + hashlib.sha256(_canonical_bytes(structural)).hexdigest()


def scan_tree(
    client: WikiClient,
    *,
    url_or_token: str,
    root_node_token: str = "",
    max_nodes: int = 20_000,
    max_depth: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Scan a whole space or one selected subtree without partial commits."""

    if max_nodes <= 0:
        raise WikiError("WIKI_INVALID_ARGUMENT", "max_nodes 必须是正整数。")
    if max_depth is not None and max_depth < 0:
        raise WikiError("WIKI_INVALID_ARGUMENT", "max_depth 必须是非负整数。")
    anchor = client.node_get(root_node_token or url_or_token)
    space_id = str(anchor.get("space_id", "")).strip()
    anchor_token = str(anchor.get("node_token", "")).strip()
    if not space_id or not anchor_token:
        raise WikiError(
            "WIKI_NODE_INVALID",
            "无法从 Wiki URL 解析 space_id 和 node_token。",
        )

    subtree = bool(root_node_token)
    records: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[dict[str, Any], int, list[str], bool]] = deque()
    if subtree:
        title = str(anchor.get("title", "")).strip()
        queue.append((anchor, 0, [title] if title else [], True))
    else:
        # A Wiki homepage can report has_child=false while the space still has
        # many independent root nodes. Space roots must always be listed.
        for item in client.node_list(space_id):
            title = str(item.get("title", "")).strip()
            queue.append((item, 0, [title] if title else [], False))

    expanded = 0
    truncated_by_depth = 0
    while queue:
        value, depth, path_titles, force_expand = queue.popleft()
        token = str(value.get("node_token", "")).strip()
        if not token or token in records:
            continue
        if len(records) >= max_nodes:
            raise WikiError(
                "WIKI_SCAN_LIMIT_EXCEEDED",
                f"Wiki 扫描超过 max_nodes={max_nodes}，未保存不完整结果。",
                hint="选择更小的子树，或显式提高 max_nodes。",
            )
        records[token] = _node_record(value, depth=depth, path_titles=path_titles)
        should_expand = force_expand or bool(value.get("has_child"))
        if not should_expand:
            continue
        if max_depth is not None and depth >= max_depth:
            truncated_by_depth += 1
            continue
        children = client.node_list(space_id, token)
        expanded += 1
        if progress and expanded % 100 == 0:
            progress(f"expanded={expanded} nodes={len(records)}")
        for child in children:
            child_title = str(child.get("title", "")).strip()
            queue.append(
                (
                    child,
                    depth + 1,
                    [*path_titles, child_title] if child_title else list(path_titles),
                    False,
                )
            )

    nodes = sorted(
        records.values(),
        key=lambda item: (item["path_titles"], item["node_token"]),
    )
    scope = "subtree" if subtree else "baseline"
    return {
        "schema_version": TREE_SCHEMA,
        "space": {
            "space_id": space_id,
            "source_url": url_or_token,
        },
        "scope": {
            "kind": scope,
            "root_node_token": anchor_token if subtree else "",
            "root_title": str(anchor.get("title", "")).strip() if subtree else "",
        },
        "captured_at": _utc_now(),
        "coverage": {
            "complete": truncated_by_depth == 0,
            "node_count": len(nodes),
            "expanded_node_count": expanded,
            "max_depth_seen": max((item["depth"] for item in nodes), default=0),
            "truncated_by_depth": truncated_by_depth,
        },
        "tree_hash": _tree_hash(nodes),
        "nodes": nodes,
    }


def wiki_state_path(
    kb: Path,
    *,
    space_id: str,
    root_node_token: str = "",
) -> Path:
    root = kb.resolve() / "state" / "wiki" / space_id
    if not root_node_token:
        return root / "baseline.json"
    digest = hashlib.sha256(root_node_token.encode("utf-8")).hexdigest()
    return root / "subtrees" / f"{digest}.json"


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _ensure_regenerable_state_ignored(kb: Path) -> None:
    info_exclude = kb.resolve() / ".git" / "info" / "exclude"
    if not info_exclude.parent.is_dir():
        return
    current = info_exclude.read_text(encoding="utf-8") if info_exclude.exists() else ""
    if any(line.strip() == "/state/" for line in current.splitlines()):
        return
    info_exclude.write_text(
        current + ("" if not current or current.endswith("\n") else "\n") + "/state/\n",
        encoding="utf-8",
    )


def diff_snapshots(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    old_nodes = {
        item["node_token"]: item
        for item in (previous or {}).get("nodes", [])
        if isinstance(item, Mapping) and item.get("node_token")
    }
    new_nodes = {
        item["node_token"]: item
        for item in current.get("nodes", [])
        if isinstance(item, Mapping) and item.get("node_token")
    }
    added = sorted(set(new_nodes) - set(old_nodes))
    left = sorted(set(old_nodes) - set(new_nodes))
    comparable_fields = (
        "node_token",
        "obj_token",
        "obj_type",
        "title",
        "parent_node_token",
        "has_child",
        "updated_at",
        "obj_edit_time",
    )
    changed = sorted(
        token
        for token in set(old_nodes) & set(new_nodes)
        if _canonical_bytes(
            {field: old_nodes[token].get(field) for field in comparable_fields}
        )
        != _canonical_bytes(
            {field: new_nodes[token].get(field) for field in comparable_fields}
        )
    )
    return {
        "added_count": len(added),
        "changed_count": len(changed),
        "left_subtree_count": len(left),
        "added_node_tokens": added,
        "changed_node_tokens": changed,
        "left_subtree_node_tokens": left,
    }


def enrich_snapshot_metadata(
    client: WikiClient,
    snapshot: dict[str, Any],
    *,
    mode: str,
    previous: Mapping[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Apply the Profile's explicit metadata-request policy in place."""

    if mode not in CHANGE_DETECTION_MODES:
        raise WikiError(
            "WIKI_INVALID_ARGUMENT",
            f"未知 change_detection mode: {mode}",
        )
    if mode == "structure_only":
        return
    previous_tokens = {
        str(item.get("node_token", ""))
        for item in (previous or {}).get("nodes", [])
        if isinstance(item, Mapping)
    }
    eligible = [
        item
        for item in snapshot.get("nodes", [])
        if isinstance(item, dict)
        and item.get("obj_type") in SUPPORTED_DOCUMENT_TYPES
        and (
            mode == "new_and_updated"
            or (previous is not None and item.get("node_token") not in previous_tokens)
        )
    ]
    # On the first new_pages scan there is no baseline for "new"; avoid turning
    # profile creation into an implicit full metadata crawl.
    for index, item in enumerate(eligible, start=1):
        detail = client.node_get(str(item["node_token"]))
        for field in (
            "node_create_time",
            "obj_create_time",
            "obj_edit_time",
            "updated_at",
        ):
            if detail.get(field) not in (None, ""):
                item[field] = str(detail[field])
        if progress and index % 100 == 0:
            progress(f"metadata={index}/{len(eligible)}")
    snapshot["metadata_policy"] = {
        "mode": mode,
        "requested_node_count": len(eligible),
    }


def save_snapshot(kb: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    coverage = snapshot.get("coverage", {})
    if not isinstance(coverage, Mapping) or not coverage.get("complete"):
        raise WikiError(
            "WIKI_SCAN_INCOMPLETE",
            "扫描覆盖不完整，拒绝替换已保存的 Wiki 状态。",
            hint="缩小子树或移除 max_depth 后重试。",
        )
    space = snapshot.get("space", {})
    scope = snapshot.get("scope", {})
    path = wiki_state_path(
        kb,
        space_id=str(space.get("space_id", "")),
        root_node_token=str(scope.get("root_node_token", "")),
    )
    previous: Mapping[str, Any] | None = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WikiError(
                "WIKI_STATE_INVALID",
                f"已保存的 Wiki 状态不是合法 JSON: {path}",
            ) from exc
        if isinstance(loaded, Mapping):
            previous = loaded
    delta = diff_snapshots(previous, snapshot)
    _ensure_regenerable_state_ignored(kb)
    _atomic_json_write(path, snapshot)
    return {
        "state_path": str(path),
        "space_id": space.get("space_id"),
        "scope": scope.get("kind"),
        "root_node_token": scope.get("root_node_token"),
        "node_count": snapshot["coverage"]["node_count"],
        "tree_hash": snapshot.get("tree_hash"),
        "delta": {
            "added_count": delta["added_count"],
            "changed_count": delta["changed_count"],
            "left_subtree_count": delta["left_subtree_count"],
        },
    }


def load_snapshot(
    kb: Path,
    *,
    space_id: str,
    root_node_token: str = "",
) -> dict[str, Any]:
    path = wiki_state_path(
        kb,
        space_id=space_id,
        root_node_token=root_node_token,
    )
    if not path.is_file():
        raise WikiError(
            "WIKI_STATE_NOT_FOUND",
            "尚未保存这个 Wiki 空间或子树的扫描状态。",
            hint="先运行 wiki scan。",
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WikiError("WIKI_STATE_INVALID", f"Wiki 状态损坏: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != TREE_SCHEMA:
        raise WikiError("WIKI_STATE_INVALID", f"Wiki 状态 schema 不受支持: {path}")
    return value


def topic_summary(snapshot: Mapping[str, Any], *, limit: int = 30) -> dict[str, Any]:
    if limit <= 0:
        raise WikiError("WIKI_INVALID_ARGUMENT", "limit 必须是正整数。")
    nodes = [item for item in snapshot.get("nodes", []) if isinstance(item, Mapping)]
    if snapshot.get("scope", {}).get("kind") == "subtree":
        topic_depth = 1
        root_token = str(snapshot.get("scope", {}).get("root_node_token", ""))
        topic_nodes = [
            item for item in nodes if int(item.get("depth", -1)) == topic_depth
        ]
        if not topic_nodes:
            topic_nodes = [item for item in nodes if item.get("node_token") == root_token]
    else:
        topic_depth = 0
        topic_nodes = [item for item in nodes if int(item.get("depth", -1)) == 0]

    children_by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for item in nodes:
        parent = str(item.get("parent_node_token", ""))
        children_by_parent.setdefault(parent, []).append(item)
    topics = []
    for topic in topic_nodes:
        token = str(topic.get("node_token", ""))
        descendants: list[Mapping[str, Any]] = []
        pending = [topic]
        seen: set[str] = set()
        while pending:
            item = pending.pop()
            item_token = str(item.get("node_token", ""))
            if not item_token or item_token in seen:
                continue
            seen.add(item_token)
            descendants.append(item)
            pending.extend(children_by_parent.get(item_token, []))
        topics.append(
            {
                "node_token": token,
                "title": str(topic.get("title", "")).strip() or "(untitled)",
                "descendant_count": max(0, len(descendants) - 1),
                "document_count": sum(
                    1 for item in descendants if item.get("obj_type") in SUPPORTED_DOCUMENT_TYPES
                ),
                "max_depth": max(
                    (int(item.get("depth", 0)) for item in descendants),
                    default=int(topic.get("depth", 0)),
                ),
            }
        )
    topics.sort(key=lambda item: (-item["document_count"], item["title"], item["node_token"]))
    return {
        "space_id": snapshot.get("space", {}).get("space_id"),
        "scope": snapshot.get("scope"),
        "total_topics": len(topics),
        "truncated": len(topics) > limit,
        "topics": topics[:limit],
    }


def select_candidates(
    client: WikiClient,
    snapshot: Mapping[str, Any],
    *,
    max_pages: int = 500,
    updated_after: datetime | None = None,
) -> dict[str, Any]:
    candidates = [
        item
        for item in snapshot.get("nodes", [])
        if isinstance(item, Mapping)
        and item.get("obj_type") in SUPPORTED_DOCUMENT_TYPES
        and str(item.get("title", "")).strip()
        and str(item.get("obj_token", "")).strip()
    ]
    by_document: dict[str, Mapping[str, Any]] = {}
    for item in candidates:
        by_document.setdefault(str(item["obj_token"]), item)
    if len(by_document) > max_pages:
        raise WikiError(
            "WIKI_CANDIDATE_LIMIT_EXCEEDED",
            f"当前子树有 {len(by_document)} 个可读文档，超过 max_pages={max_pages}。",
            hint="选择更小的子树，或在确认 token 成本后显式提高 max_pages。",
        )

    source_url = str(snapshot.get("space", {}).get("source_url", "")).strip()
    split = urlsplit(source_url)
    wiki_origin = (
        f"{split.scheme}://{split.netloc}"
        if split.scheme == "https" and split.netloc
        else "https://www.larksuite.com"
    )
    pages = []
    for document_id, item in sorted(
        by_document.items(),
        key=lambda pair: (list(pair[1].get("path_titles", [])), pair[0]),
    ):
        detail = client.node_get(str(item["node_token"]))
        updated_at = str(detail.get("updated_at", "")).strip()
        if not updated_at and detail.get("obj_edit_time"):
            updated_at = datetime.fromtimestamp(
                int(str(detail["obj_edit_time"])),
                tz=timezone.utc,
            ).isoformat()
        if updated_after:
            try:
                parsed = (
                    datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    if updated_at
                    else None
                )
            except ValueError:
                parsed = None
            if parsed is None or parsed < updated_after:
                continue
        pages.append(
            {
                "document_id": document_id,
                "node_token": str(item["node_token"]),
                "title": str(item["title"]).strip(),
                "url": (
                    f"{wiki_origin}/wiki/{item['node_token']}"
                ),
                "obj_type": str(item["obj_type"]),
                "updated_at": updated_at,
                "path_titles": list(item.get("path_titles", [])),
            }
        )
    return {
        "schema_version": "byteworker-wiki-candidate-selection/v1",
        "space_id": snapshot.get("space", {}).get("space_id"),
        "space_url": source_url,
        "root_node_token": snapshot.get("scope", {}).get("root_node_token"),
        "tree_hash": snapshot.get("tree_hash"),
        "selected_at": _utc_now(),
        "page_count": len(pages),
        "pages": pages,
    }


def write_selection(path: Path, selection: Mapping[str, Any]) -> None:
    if path.exists() and path.is_dir():
        raise WikiError("WIKI_INVALID_ARGUMENT", f"输出路径是目录: {path}")
    _atomic_json_write(path.resolve(), selection)
