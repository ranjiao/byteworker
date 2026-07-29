#!/usr/bin/env python3
"""Inspect and capture supported read-only routine sources."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_capture import (  # noqa: E402
    DEFAULT_MAX_ITEMS,
    CommandRunner,
    SourceCaptureError,
    base_auth_status,
    capture_base,
    capture_meego,
    diff_captures,
    inspect_base,
    inspect_meego,
    meego_auth_status,
    read_capture,
    write_capture,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="检查 Meego / 多维表格授权与视图，并生成只读规范快照"
    )
    sub = result.add_subparsers(dest="operation", required=True)
    auth = sub.add_parser(
        "auth-status",
        help="只读检查来源登录与最小授权，不发起 OAuth",
    )
    auth.add_argument(
        "--source-type",
        choices=("meego", "feishu_base"),
        required=True,
    )
    auth.add_argument(
        "--host",
        default="",
        help="Meego 站点；例如 project.feishu.cn 或 meegle.com",
    )
    auth.add_argument(
        "--timeout",
        type=_positive_int,
        default=30,
        help="底层认证状态检查超时秒数",
    )
    for name in ("inspect", "capture"):
        command = sub.add_parser(name)
        command.add_argument(
            "--source-type",
            choices=("meego", "feishu_base"),
            required=True,
        )
        command.add_argument("--url", default="")
        command.add_argument("--project-key", default="")
        command.add_argument("--base-token", default="")
        command.add_argument("--table-id", default="")
        command.add_argument("--view-id", default="")
        command.add_argument(
            "--field",
            action="append",
            default=[],
            help="稳定字段 key/ID/精确名称，可重复",
        )
        command.add_argument(
            "--timeout",
            type=_positive_int,
            default=180,
            help="单次底层 CLI 超时秒数",
        )
        if name == "capture":
            command.add_argument(
                "--max-items",
                type=_positive_int,
                default=DEFAULT_MAX_ITEMS,
            )
            command.add_argument(
                "--out",
                help="完整快照输出路径；必须位于临时目录或知识库目录",
            )
    diff = sub.add_parser(
        "diff",
        help="按稳定记录 ID 比较相邻完整快照；left_view 不等于删除",
    )
    diff.add_argument("--current", required=True, help="当前 capture JSON")
    diff.add_argument("--previous", default="", help="上一份 capture JSON；首轮可省略")
    diff.add_argument("--out", help="差异 JSON 输出路径")
    return result


def _runner(source_type: str, timeout: int) -> CommandRunner:
    if source_type == "meego":
        binary = os.environ.get("BYTEWORKER_MEEGLE_BIN", "meegle")
    else:
        binary = os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli")
    return CommandRunner(binary, timeout_seconds=timeout)


def _run(args: argparse.Namespace) -> dict:
    if args.operation == "diff":
        current = read_capture(Path(args.current))
        previous = read_capture(Path(args.previous)) if args.previous else None
        return diff_captures(current=current, previous=previous)
    runner = _runner(args.source_type, args.timeout)
    if args.operation == "auth-status":
        if args.source_type == "meego":
            return meego_auth_status(runner=runner, host=args.host)
        return base_auth_status(runner=runner)

    common = {
        "runner": runner,
        "url": args.url,
        "view_id": args.view_id,
    }
    if args.source_type == "meego":
        common["project_key"] = args.project_key
        if args.operation == "inspect":
            return inspect_meego(**common, fields=args.field)
        return capture_meego(
            **common,
            fields=args.field,
            max_items=args.max_items,
        )

    common.update(
        {
            "base_token": args.base_token,
            "table_id": args.table_id,
        }
    )
    if args.operation == "inspect":
        return inspect_base(**common)
    return capture_base(
        **common,
        fields=args.field,
        max_items=args.max_items,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = _run(args)
        if args.operation in {"capture", "diff"} and args.out:
            output = Path(args.out).expanduser().resolve()
            write_capture(output, result, skill_root=ROOT)
            if args.operation == "capture":
                result = {
                    "schema_version": result["schema_version"],
                    "source_type": result["source_type"],
                    "source_uid": result["source_uid"],
                    "title": result["title"],
                    "output": str(output),
                    "content_hash": result["content_hash"],
                    "item_count": result["pagination"]["item_count"],
                    "complete": result["pagination"]["complete"],
                    "sanitization": result.get("sanitization", {}),
                }
            else:
                result = {
                    "schema_version": result["schema_version"],
                    "source_type": result["source_type"],
                    "source_uid": result["source_uid"],
                    "output": str(output),
                    "diff_hash": result["diff_hash"],
                    "summary": result["summary"],
                }
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except SourceCaptureError as exc:
        print(
            json.dumps(
                {"error": exc.as_dict()},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
