#!/usr/bin/env python3
"""On-demand Feishu Wiki space exploration CLI."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_profiles import SourceProfileError, load_profile, save_profile  # noqa: E402
from wiki_explorer import (  # noqa: E402
    CHANGE_DETECTION_MODES,
    LarkWikiClient,
    WikiError,
    load_snapshot,
    enrich_snapshot_metadata,
    save_snapshot,
    scan_tree,
    select_candidates,
    topic_summary,
    write_selection,
)


def _print(value: object, *, pretty: bool = False) -> None:
    json.dump(
        value,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
    )
    sys.stdout.write("\n")


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pretty", action="store_true")
    sub = result.add_subparsers(dest="operation", required=True)

    sub.add_parser("auth-status")

    inspect = sub.add_parser("inspect")
    inspect.add_argument("--url", required=True)

    scan = sub.add_parser("scan")
    scan.add_argument("--kb", required=True, type=Path)
    scan_source = scan.add_mutually_exclusive_group(required=True)
    scan_source.add_argument("--url")
    scan_source.add_argument("--source-uid")
    scan.add_argument("--root-node-token", default="")
    scan.add_argument("--max-nodes", type=_positive)
    scan.add_argument("--max-depth", type=int)

    topics = sub.add_parser("topics")
    topics.add_argument("--kb", required=True, type=Path)
    topics.add_argument("--space-id", required=True)
    topics.add_argument("--root-node-token", default="")
    topics.add_argument("--limit", type=_positive, default=30)

    candidates = sub.add_parser("candidates")
    candidates.add_argument("--kb", required=True, type=Path)
    candidates.add_argument("--space-id", required=True)
    candidates.add_argument("--root-node-token", default="")
    candidates.add_argument("--max-pages", type=_positive, default=500)
    candidates.add_argument("--updated-after")
    candidates.add_argument("--out", required=True, type=Path)
    candidates.add_argument("--preview-limit", type=_positive, default=20)

    profile = sub.add_parser("profile-create")
    profile.add_argument("--kb", required=True, type=Path)
    profile.add_argument("--url", required=True)
    profile.add_argument("--root-node-token", required=True)
    profile.add_argument(
        "--routine",
        choices=("off", "daily", "weekly", "monthly"),
        default="off",
    )
    profile.add_argument(
        "--change-detection",
        choices=sorted(CHANGE_DETECTION_MODES),
        default="structure_only",
    )
    profile.add_argument("--max-nodes", type=_positive, default=20_000)
    profile.add_argument("--max-depth", type=int)
    return result


def _updated_after(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WikiError(
            "WIKI_INVALID_ARGUMENT",
            "updated-after 必须是带时区的 ISO8601 时间。",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WikiError(
            "WIKI_INVALID_ARGUMENT",
            "updated-after 必须包含时区。",
        )
    return parsed


def _validate_kb(kb: Path) -> Path:
    resolved = kb.expanduser().resolve()
    if not resolved.is_dir():
        raise WikiError("WIKI_KB_INVALID", f"知识库目录不存在: {resolved}")
    if resolved == ROOT or ROOT in resolved.parents:
        raise WikiError(
            "WIKI_KB_INVALID",
            "Wiki 业务状态不得写入 byteworker skill 仓库。",
        )
    return resolved


def _validate_output(path: Path, kb: Path) -> Path:
    resolved = path.expanduser().resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if not (
        resolved == kb
        or kb in resolved.parents
        or resolved == temporary
        or temporary in resolved.parents
    ):
        raise WikiError(
            "WIKI_OUTPUT_PATH_DENIED",
            "候选文件只能写入系统临时目录或知识库目录。",
        )
    if resolved == ROOT or ROOT in resolved.parents:
        raise WikiError(
            "WIKI_OUTPUT_PATH_DENIED",
            "候选文件不得写入 byteworker skill 仓库。",
        )
    return resolved


def _run(args: argparse.Namespace) -> object:
    client = LarkWikiClient()
    if args.operation == "auth-status":
        return client.auth_status()
    if args.operation == "inspect":
        node = client.node_get(args.url)
        return {
            "space_id": node.get("space_id"),
            "node_token": node.get("node_token"),
            "title": node.get("title"),
            "obj_type": node.get("obj_type"),
            "has_child": node.get("has_child"),
            "identity": "user",
        }
    if args.operation == "scan":
        kb = _validate_kb(args.kb)
        change_detection = "structure_only"
        if args.source_uid:
            if args.root_node_token or args.max_nodes is not None or args.max_depth is not None:
                raise WikiError(
                    "WIKI_INVALID_ARGUMENT",
                    "按 source-uid 刷新时不得用 CLI 覆盖子树或扫描上限。",
                    hint="需要改变口径时保存新的 Wiki Profile revision。",
                )
            profile = load_profile(kb, args.source_uid)
            if profile["source_type"] != "feishu_wiki":
                raise WikiError(
                    "WIKI_PROFILE_IDENTITY_MISMATCH",
                    "source profile 不是 feishu_wiki。",
                )
            url = profile["source_url"]
            root_node_token = profile["selector"]["root_node_token"]
            max_nodes = profile["capture_policy"]["max_nodes"]
            max_depth = profile["capture_policy"]["max_depth"]
            change_detection = profile["capture_policy"]["change_detection"]
        else:
            url = args.url
            root_node_token = args.root_node_token
            max_nodes = args.max_nodes or 20_000
            max_depth = args.max_depth
        snapshot = scan_tree(
            client,
            url_or_token=url,
            root_node_token=root_node_token,
            max_nodes=max_nodes,
            max_depth=max_depth,
            progress=lambda value: print(value, file=sys.stderr),
        )
        previous = None
        try:
            previous = load_snapshot(
                kb,
                space_id=snapshot["space"]["space_id"],
                root_node_token=snapshot["scope"]["root_node_token"],
            )
        except WikiError as exc:
            if exc.code != "WIKI_STATE_NOT_FOUND":
                raise
        enrich_snapshot_metadata(
            client,
            snapshot,
            mode=change_detection,
            previous=previous,
            progress=lambda value: print(value, file=sys.stderr),
        )
        return save_snapshot(kb, snapshot)
    if args.operation == "topics":
        kb = _validate_kb(args.kb)
        snapshot = load_snapshot(
            kb,
            space_id=args.space_id,
            root_node_token=args.root_node_token,
        )
        return topic_summary(snapshot, limit=args.limit)
    if args.operation == "candidates":
        kb = _validate_kb(args.kb)
        snapshot = load_snapshot(
            kb,
            space_id=args.space_id,
            root_node_token=args.root_node_token,
        )
        selection = select_candidates(
            client,
            snapshot,
            max_pages=args.max_pages,
            updated_after=_updated_after(args.updated_after),
        )
        output = _validate_output(args.out, kb)
        write_selection(output, selection)
        preview = selection["pages"][: args.preview_limit]
        return {
            "selection_path": str(output),
            "space_id": selection["space_id"],
            "root_node_token": selection["root_node_token"],
            "page_count": selection["page_count"],
            "preview_truncated": len(preview) < selection["page_count"],
            "preview": [
                {
                    "document_id": item["document_id"],
                    "title": item["title"],
                    "updated_at": item["updated_at"],
                    "path_titles": item["path_titles"],
                }
                for item in preview
            ],
        }
    if args.operation == "profile-create":
        kb = _validate_kb(args.kb)
        node = client.node_get(args.root_node_token)
        space_id = str(node.get("space_id", "")).strip()
        node_token = str(node.get("node_token", "")).strip()
        if not space_id or not node_token:
            raise WikiError(
                "WIKI_NODE_INVALID",
                "无法解析子树节点的 space_id/node_token。",
            )
        capture_policy = {
            "max_depth": args.max_depth,
            "max_nodes": args.max_nodes,
            "include_types": ["doc", "docx"],
            "change_detection": args.change_detection,
        }
        profile = {
            "schema_version": "byteworker-source-profile/v2",
            "source_type": "feishu_wiki",
            "source_uid": f"feishu_wiki:{space_id}:{node_token}",
            "source_url": args.url,
            "title": str(node.get("title", "")).strip() or node_token,
            "selector": {
                "space_id": space_id,
                "root_node_token": node_token,
            },
            "capture_policy": capture_policy,
            "routine": {
                "enabled": args.routine != "off",
                "cadence": None if args.routine == "off" else args.routine,
            },
        }
        receipt = save_profile(kb, profile, skill_root=ROOT)
        return {
            "source_uid": profile["source_uid"],
            "space_id": space_id,
            "root_node_token": node_token,
            "profile": receipt,
        }
    raise AssertionError(args.operation)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _print(_run(args), pretty=args.pretty)
    except WikiError as exc:
        _print({"error": exc.as_dict()}, pretty=args.pretty)
        return 1
    except SourceProfileError as exc:
        value = {
            "code": exc.code,
            "message": str(exc),
        }
        if exc.hint:
            value["hint"] = exc.hint
        _print({"error": value}, pretty=args.pretty)
        return 1
    except (OSError, ValueError) as exc:
        _print(
            {
                "error": {
                    "code": "WIKI_INPUT_ERROR",
                    "message": str(exc),
                }
            },
            pretty=args.pretty,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
