#!/usr/bin/env python3
"""Inspect and capture supported read-only routine sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_capture import SourceCaptureError  # noqa: E402
from source_cli_service import persist_result, run  # noqa: E402
from source_operations import (  # noqa: E402
    operation_source_types,
    source_operation_arguments,
)
from source_profiles import (  # noqa: E402
    PROFILE_SOURCE_TYPES,
    SourceProfileError,
)
from sources import (  # noqa: E402
    SourceBundleError,
    SourceRegistryError,
    create_default_registry,
)


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="统一来源授权、抓取、Profile、SourceBundle 与快照差异入口"
    )
    sub = result.add_subparsers(dest="operation", required=True)
    sub.add_parser(
        "capabilities",
        help="列出当前实现的 operation、Profile 与 SourceBundle 能力",
    )
    bundle_spec = sub.add_parser(
        "bundle-spec",
        help="输出指定 SourceBundle adapter 的机器可读 request 契约",
    )
    bundle_spec.add_argument(
        "--source-type",
        choices=create_default_registry().source_types(),
        required=True,
    )
    operation_help = {
        "auth-status": "只读检查来源登录与最小授权，不发起 OAuth",
        "inspect": "解析来源坐标、字段和规模，不写入 KB",
        "capture": "执行有界完整抓取，可选择输出 SourceBundle",
        "register": "验证并保存 provider source profile",
    }
    for name, help_text in operation_help.items():
        command = sub.add_parser(name, help=help_text)
        command.add_argument(
            "--source-type",
            choices=operation_source_types(name),
            required=True,
        )
        for argument_spec in source_operation_arguments(name):
            argument_spec.add_to(command)
    bundle = sub.add_parser(
        "bundle",
        help="由 provider adapter 把已抓取材料规范化为 SourceBundle v2",
    )
    bundle.add_argument(
        "--source-type",
        choices=create_default_registry().source_types(),
        required=True,
    )
    bundle.add_argument(
        "--request",
        required=True,
        help=(
            "provider 专属 request JSON 文件路径；不接受内联 JSON，"
            "文件必须位于临时目录或知识库目录"
        ),
    )
    bundle.add_argument(
        "--out",
        required=True,
        help="SourceBundle v2 输出路径",
    )
    profile = sub.add_parser("profile", help="读取一个 KB source profile")
    profile.add_argument("--kb", required=True)
    profile.add_argument("--source-uid", required=True)
    profile_save = sub.add_parser(
        "profile-save",
        help="严格校验并把一个 v1/v2 source profile 写入 KB",
    )
    profile_save.add_argument("--kb", required=True)
    profile_save.add_argument(
        "--file",
        required=True,
        help="系统临时目录中的 source profile JSON",
    )
    profiles = sub.add_parser("profiles", help="列出 KB 的 source profiles")
    profiles.add_argument("--kb", required=True)
    profiles.add_argument(
        "--source-type",
        choices=tuple(sorted(PROFILE_SOURCE_TYPES)),
        default="",
    )
    diff = sub.add_parser(
        "diff",
        help="按稳定记录 ID 比较相邻完整快照；left_view 不等于删除",
    )
    diff.add_argument("--current", required=True, help="当前 capture JSON")
    diff.add_argument("--previous", default="", help="上一份 capture JSON；首轮可省略")
    diff.add_argument(
        "--kb",
        default="",
        help="知识库数据目录；提供后从已提交 raw 读取上一份 snapshot",
    )
    diff.add_argument(
        "--source-uid",
        default="",
        help="显式校验当前 capture 的稳定来源 ID",
    )
    diff.add_argument(
        "--raw-id",
        default="",
        help="显式选择 KB 中的历史 raw；仅和 --kb 一起使用",
    )
    diff.add_argument(
        "--history-index",
        type=_nonnegative_int,
        default=0,
        help="选择第 N 个历史 snapshot；0 为最新，仅和 --kb 一起使用",
    )
    diff.add_argument("--out", help="差异 JSON 输出路径")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = persist_result(args, run(args, skill_root=ROOT), skill_root=ROOT)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (
        SourceBundleError,
        SourceCaptureError,
        SourceProfileError,
        SourceRegistryError,
    ) as exc:
        if isinstance(exc, (SourceBundleError, SourceCaptureError)):
            error = exc.as_dict()
        elif isinstance(exc, SourceProfileError):
            error = {
                "code": exc.code,
                "message": str(exc),
                **({"hint": exc.hint} if exc.hint else {}),
            }
        else:
            error = {
                "code": exc.code,
                "message": str(exc),
            }
        print(
            json.dumps(
                {"error": error},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
