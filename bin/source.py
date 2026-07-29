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
    aeolus_client_from_environment,
    aeolus_auth_status,
    base_auth_status,
    build_aeolus_profile,
    capture_aeolus,
    capture_aeolus_from_profile,
    capture_base,
    capture_meego,
    diff_captures,
    inspect_aeolus,
    inspect_base,
    inspect_meego,
    meego_auth_status,
    read_capture,
    write_capture,
)
from source_profiles import (  # noqa: E402
    SourceProfileError,
    list_profiles,
    load_profile,
    profile_relative_path,
    profile_revision,
    save_profile,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="检查 Meego / 多维表格 / 风神授权与视图，并生成只读规范快照"
    )
    sub = result.add_subparsers(dest="operation", required=True)
    auth = sub.add_parser(
        "auth-status",
        help="只读检查来源登录与最小授权，不发起 OAuth",
    )
    auth.add_argument(
        "--source-type",
        choices=("meego", "feishu_base", "aeolus"),
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
            choices=("meego", "feishu_base", "aeolus"),
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
    profiles = sub.add_parser("profiles", help="列出 KB 的 source profiles")
    profiles.add_argument("--kb", required=True)
    profiles.add_argument("--source-type", choices=("aeolus",), default="")
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


def _where_filters(values: list[str]) -> list[dict]:
    result = []
    for raw in values:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SourceCaptureError(
                "SOURCE_FILTER_INVALID",
                f"--where 不是合法 JSON: {raw}",
            ) from exc
        if not isinstance(value, dict):
            raise SourceCaptureError(
                "SOURCE_FILTER_INVALID",
                "--where 顶层必须是 JSON 对象",
            )
        result.append(value)
    return result


def _run(args: argparse.Namespace) -> dict:
    if args.operation == "diff":
        current = read_capture(Path(args.current))
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
    if args.source_type == "aeolus":
        client = aeolus_client_from_environment(
            timeout_seconds=args.timeout,
        )
    else:
        runner = _runner(args.source_type, args.timeout)
    if args.operation == "auth-status":
        if args.source_type == "meego":
            return meego_auth_status(runner=runner, host=args.host)
        if args.source_type == "aeolus":
            return aeolus_auth_status(client=client)
        return base_auth_status(runner=runner)

    if args.operation == "register":
        profile = build_aeolus_profile(
            client=client,
            url=args.url,
            report_ids=args.report_id,
            where_filters=_where_filters(args.where),
            filter_mode=args.filter_mode,
            max_items=args.max_items,
            routine="" if args.routine == "off" else args.routine,
        )
        receipt = save_profile(
            Path(args.kb).expanduser(),
            profile,
            skill_root=ROOT,
        )
        return {
            **receipt,
            "profile": profile,
        }

    if args.source_type == "aeolus":
        if args.field:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "风神使用 --report-id 选择报表，不使用 --field",
            )
        if args.operation == "inspect":
            if args.report_id or args.where or args.filter_mode != "dashboard":
                raise SourceCaptureError(
                    "SOURCE_ARGUMENT_INVALID",
                    "风神 inspect 只解析 dashboard；报表与筛选选择在 capture 时指定",
                )
            return inspect_aeolus(client=client, url=args.url)
        if args.source_uid:
            if not args.kb:
                raise SourceCaptureError(
                    "SOURCE_ARGUMENT_INVALID",
                    "--source-uid 必须同时提供 --kb",
                )
            if (
                args.url
                or args.report_id
                or args.where
                or args.filter_mode != "dashboard"
                or args.max_items != DEFAULT_MAX_ITEMS
            ):
                raise SourceCaptureError(
                    "SOURCE_ARGUMENT_INVALID",
                    "按 --source-uid 抓取时不得用 CLI 覆盖 URL、报表、筛选或行数",
                    hint="需要改变口径时重新运行 source register，形成新的 profile revision。",
                )
            profile = load_profile(
                Path(args.kb).expanduser(),
                args.source_uid,
            )
            result = capture_aeolus_from_profile(
                client=client,
                profile=profile,
            )
            result["source_profile"]["path"] = str(
                profile_relative_path(profile)
            )
            return result
        if args.kb:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--kb 仅和 --source-uid 一起使用",
            )
        return capture_aeolus(
            client=client,
            url=args.url,
            report_ids=args.report_id,
            where_filters=_where_filters(args.where),
            filter_mode=args.filter_mode,
            max_items=args.max_items,
        )

    if args.report_id or args.where or args.filter_mode != "dashboard":
        raise SourceCaptureError(
            "SOURCE_ARGUMENT_INVALID",
            "--report-id / --where / --filter-mode 仅用于 source_type=aeolus",
        )

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
    except (SourceCaptureError, SourceProfileError) as exc:
        error = (
            exc.as_dict()
            if isinstance(exc, SourceCaptureError)
            else {
                "code": exc.code,
                "message": str(exc),
                **({"hint": exc.hint} if exc.hint else {}),
            }
        )
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
