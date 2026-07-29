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

from source_capture import (  # noqa: E402
    DEFAULT_MAX_ITEMS,
    SourceCaptureError,
    diff_captures,
    read_capture,
    write_capture,
    write_capture_pair,
)
from source_operations import (  # noqa: E402
    run_source_operation,
    source_operation_types,
)
from source_profiles import (  # noqa: E402
    PROFILE_SOURCE_TYPES,
    SourceProfileError,
    list_profiles,
    load_profile,
    profile_relative_path,
    profile_revision,
    save_profile,
)
from snapshot_store import diff_current_against_kb  # noqa: E402
from sources import (  # noqa: E402
    BUNDLE_SCHEMA,
    SourceBundleError,
    SourceRegistryError,
    create_default_registry,
    ensure_source_request_safe,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


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
    auth = sub.add_parser(
        "auth-status",
        help="只读检查来源登录与最小授权，不发起 OAuth",
    )
    auth.add_argument(
        "--source-type",
        choices=source_operation_types(),
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
            choices=source_operation_types(),
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
            "--report-id",
            action="append",
            type=_positive_int,
            default=[],
            help="风神 sheet 内的报表 ID，可重复；默认读取全部报表",
        )
        command.add_argument(
            "--filter-mode",
            choices=("dashboard", "explicit", "merge"),
            default="dashboard",
            help="风神筛选策略：重放看板、完全固定、或覆盖看板默认值",
        )
        command.add_argument(
            "--where",
            action="append",
            default=[],
            help="风神筛选 JSON，可重复；explicit/merge 使用",
        )
        command.add_argument(
            "--timeout",
            type=_positive_int,
            default=180,
            help="单次来源读取超时秒数",
        )
        if name == "capture":
            command.add_argument(
                "--kb",
                default="",
                help="知识库数据目录；和 --source-uid 一起按已保存 profile 抓取",
            )
            command.add_argument(
                "--source-uid",
                default="",
                help="KB 中已注册的稳定数据源 ID",
            )
            command.add_argument(
                "--max-items",
                type=_positive_int,
                default=DEFAULT_MAX_ITEMS,
            )
            command.add_argument(
                "--out",
                help="完整快照输出路径；必须位于临时目录或知识库目录",
            )
            command.add_argument(
                "--bundle-out",
                default="",
                help=(
                    "同时把完整 capture 转为 SourceBundle v2；"
                    "必须与 --out 一起使用"
                ),
            )
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
            "provider 专属构造参数 JSON；业务文件必须位于临时目录或知识库目录"
        ),
    )
    bundle.add_argument(
        "--out",
        required=True,
        help="SourceBundle v2 输出路径",
    )
    register = sub.add_parser(
        "register",
        help="验证并把一个风神 dashboard sheet 的独立配置写入 KB",
    )
    register.add_argument("--source-type", choices=("aeolus",), required=True)
    register.add_argument("--kb", required=True, help="知识库数据目录")
    register.add_argument("--url", required=True)
    register.add_argument(
        "--report-id",
        action="append",
        type=_positive_int,
        default=[],
        help="纳入此数据源的报表 ID；默认动态选择 sheet 全部报表",
    )
    register.add_argument(
        "--filter-mode",
        choices=("dashboard", "explicit", "merge"),
        default="dashboard",
    )
    register.add_argument("--where", action="append", default=[])
    register.add_argument(
        "--max-items",
        type=_positive_int,
        default=DEFAULT_MAX_ITEMS,
    )
    register.add_argument(
        "--routine",
        choices=("off", "daily", "weekly", "monthly"),
        default="off",
    )
    register.add_argument("--timeout", type=_positive_int, default=180)
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


def _run(args: argparse.Namespace) -> dict:
    if args.operation == "capabilities":
        return {
            "operation_source_types": list(source_operation_types()),
            "profile_source_types": sorted(PROFILE_SOURCE_TYPES),
            "bundle_source_types": list(create_default_registry().source_types()),
            "contract": BUNDLE_SCHEMA,
        }
    if args.operation == "bundle":
        request_path = Path(args.request).expanduser().resolve()
        if request_path == ROOT.resolve() or ROOT.resolve() in request_path.parents:
            raise SourceBundleError(
                "SOURCE_BUNDLE_PATH_IN_SKILL_REPO",
                "业务 bundle request 不得位于 byteworker skill 仓库",
                path=str(request_path),
            )
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceBundleError(
                "SOURCE_BUNDLE_REQUEST_INVALID",
                f"无法读取 bundle request JSON: {request_path}",
                path=str(request_path),
            ) from exc
        if not isinstance(request, dict):
            raise SourceBundleError(
                "SOURCE_BUNDLE_REQUEST_INVALID",
                "bundle request 顶层必须是 JSON 对象",
                path=str(request_path),
            )
        if "source_type" in request:
            raise SourceBundleError(
                "SOURCE_BUNDLE_REQUEST_INVALID",
                "source_type 只能由 CLI 参数声明，不得在 request 中重复",
                path="source_type",
            )
        if "capture" in request:
            raise SourceBundleError(
                "SOURCE_BUNDLE_REQUEST_INVALID",
                "bundle request 不接受内联 capture；请只提供 capture_path，"
                "避免内容与路径指向不同快照",
                path="capture",
            )
        ensure_source_request_safe(request)
        bundle = create_default_registry().build_bundle(
            args.source_type,
            **request,
            skill_root=ROOT,
        )
        return bundle.to_dict()
    if (
        args.operation == "capture"
        and args.bundle_out
        and not args.out
    ):
        raise SourceCaptureError(
            "SOURCE_ARGUMENT_INVALID",
            "--bundle-out 必须与 --out 一起使用",
        )
    if args.operation == "diff":
        current = read_capture(Path(args.current))
        if args.kb:
            if args.previous:
                raise SourceCaptureError(
                    "SOURCE_ARGUMENT_INVALID",
                    "--kb 与 --previous 不能同时使用",
                    hint="让 SnapshotStore 从 KB raw 选择上一版本，或显式提供 capture 文件。",
                )
            return diff_current_against_kb(
                current,
                Path(args.kb),
                source_uid=args.source_uid or None,
                raw_id=args.raw_id or None,
                history_index=args.history_index,
            )
        if args.source_uid or args.raw_id or args.history_index:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--source-uid / --raw-id / --history-index 必须和 --kb 一起使用",
            )
        previous = read_capture(Path(args.previous)) if args.previous else None
        return diff_captures(current=current, previous=previous)
    if args.operation == "profile":
        profile = load_profile(
            Path(args.kb).expanduser(),
            args.source_uid,
        )
        return {
            "profile": profile,
            "profile_path": str(profile_relative_path(profile)),
            "profile_revision": profile_revision(profile),
        }
    if args.operation == "profile-save":
        profile_path = Path(args.file).expanduser().resolve()
        if ROOT.resolve() == profile_path or ROOT.resolve() in profile_path.parents:
            raise SourceProfileError(
                "SOURCE_PROFILE_IN_SKILL_REPO",
                "来源实例 profile 不得放在 byteworker skill 仓库",
            )
        try:
            value = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                f"无法读取 source profile JSON: {profile_path}",
            ) from exc
        if not isinstance(value, dict):
            raise SourceProfileError(
                "SOURCE_PROFILE_INVALID",
                "source profile 顶层必须是 JSON 对象",
            )
        return save_profile(
            Path(args.kb).expanduser(),
            value,
            skill_root=ROOT,
        )
    if args.operation == "profiles":
        profiles = list_profiles(
            Path(args.kb).expanduser(),
            source_type=args.source_type,
        )
        return {
            "profiles": [
                {
                    "source_uid": profile["source_uid"],
                    "source_type": profile["source_type"],
                    "title": profile["title"],
                    "routine": profile["routine"],
                    "profile_path": str(profile_relative_path(profile)),
                    "profile_revision": profile_revision(profile),
                }
                for profile in profiles
            ],
            "count": len(profiles),
        }
    return run_source_operation(args, skill_root=ROOT)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = _run(args)
        if args.operation == "bundle":
            output = Path(args.out).expanduser().resolve()
            write_capture(output, result, skill_root=ROOT)
            result = {
                "schema_version": result["schema_version"],
                "source_type": result["identity"]["source_type"],
                "source_uid": result["identity"]["source_uid"],
                "title": result["identity"]["title"],
                "output": str(output),
                "component_count": len(result["components"]),
                "coverage": result["coverage"]["status"],
            }
        if args.operation in {"capture", "diff"} and args.out:
            output = Path(args.out).expanduser().resolve()
            if (
                args.operation == "capture"
                and result.get("schema_version") == BUNDLE_SCHEMA
                and args.bundle_out
            ):
                raise SourceCaptureError(
                    "SOURCE_ARGUMENT_INVALID",
                    "该来源 capture 已直接输出 SourceBundle，"
                    "不得再提供 --bundle-out",
                )
            if args.operation == "capture":
                if result.get("schema_version") == BUNDLE_SCHEMA:
                    write_capture(output, result, skill_root=ROOT)
                    result = {
                        "schema_version": result["schema_version"],
                        "source_type": result["identity"]["source_type"],
                        "source_uid": result["identity"]["source_uid"],
                        "title": result["identity"]["title"],
                        "output": str(output),
                        "component_count": len(result["components"]),
                        "coverage": result["coverage"]["status"],
                    }
                    print(
                        json.dumps(
                            result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    )
                    return 0
                bundle = None
                bundle_path = None
                if args.bundle_out:
                    bundle_path = Path(args.bundle_out).expanduser().resolve()
                    bundle = create_default_registry().build_bundle(
                        args.source_type,
                        capture=result,
                        capture_path=output,
                        skill_root=ROOT,
                    )
                bundle_output = ""
                if bundle is not None and bundle_path is not None:
                    write_capture_pair(
                        output,
                        result,
                        bundle_path,
                        bundle.to_dict(),
                        skill_root=ROOT,
                    )
                    bundle_output = str(bundle_path)
                else:
                    write_capture(output, result, skill_root=ROOT)
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
                    **(
                        {
                            "bundle_schema_version": BUNDLE_SCHEMA,
                            "bundle_output": bundle_output,
                        }
                        if bundle_output
                        else {}
                    ),
                }
            else:
                write_capture(output, result, skill_root=ROOT)
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
