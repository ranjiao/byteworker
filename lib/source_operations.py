"""Provider operation adapters used by the thin ``bin/source.py`` CLI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Protocol

from source_capture import (
    DEFAULT_MAX_ITEMS,
    CommandRunner,
    SourceCaptureError,
    aeolus_auth_status,
    aeolus_client_from_environment,
    base_auth_status,
    build_aeolus_profile,
    capture_aeolus,
    capture_aeolus_from_profile,
    capture_base,
    capture_meego,
    inspect_aeolus,
    inspect_base,
    inspect_meego,
    meego_auth_status,
)
from source_profiles import (
    load_profile,
    profile_relative_path,
    profile_revision,
    save_profile,
)
from source_chat_operations import FeishuChatOperations
from source_operation_contract import OperationArgument, argument, positive_int


class SourceOperationAdapter(Protocol):
    source_type: str
    operation_arguments: dict[str, tuple[OperationArgument, ...]]
    runtime_requirements: dict[str, tuple[str, ...]]

    def run(self, args: argparse.Namespace, *, skill_root: Path) -> dict[str, Any]:
        ...


HOST = argument("--host", default="", help="Meego 站点；例如 project.feishu.cn 或 meegle.com")
TIMEOUT_AUTH = argument("--timeout", type=positive_int, default=30, help="底层认证状态检查超时秒数")
TIMEOUT_READ = argument("--timeout", type=positive_int, default=180, help="单次来源读取超时秒数")
URL = argument("--url", default="")
PROJECT_KEY = argument("--project-key", default="")
BASE_TOKEN = argument("--base-token", default="")
TABLE_ID = argument("--table-id", default="")
VIEW_ID = argument("--view-id", default="")
FIELD = argument("--field", action="append", default=[], help="稳定字段 key/ID/精确名称，可重复")
REPORT_ID = argument("--report-id", action="append", type=positive_int, default=[], help="风神 sheet 内的报表 ID，可重复；默认读取全部报表")
FILTER_MODE = argument("--filter-mode", choices=("dashboard", "explicit", "merge"), default="dashboard", help="风神筛选策略：重放看板、完全固定、或覆盖看板默认值")
WHERE = argument("--where", action="append", default=[], help="风神筛选 JSON，可重复；explicit/merge 使用")
KB = argument("--kb", default="", help="知识库数据目录；和 --source-uid 一起按已保存 profile 抓取")
SOURCE_UID = argument("--source-uid", default="", help="KB 中已注册的稳定数据源 ID")
MAX_ITEMS = argument("--max-items", type=positive_int, default=DEFAULT_MAX_ITEMS)
OUT = argument("--out", help="完整快照输出路径；必须位于临时目录或知识库目录")
BUNDLE_OUT = argument("--bundle-out", default="", help="同时把完整 capture 转为 SourceBundle v2；必须与 --out 一起使用")
ROUTINE = argument("--routine", choices=("off", "daily", "weekly", "monthly"), default="off")
REGISTER_URL = argument("--url", required=True)
REGISTER_KB = argument("--kb", required=True, help="知识库数据目录")


def _runner(binary: str, timeout: int) -> CommandRunner:
    return CommandRunner(binary, timeout_seconds=timeout)


def _meego_runner(timeout: int) -> CommandRunner:
    return _runner(
        os.environ.get("BYTEWORKER_MEEGLE_BIN", "meegle"),
        timeout,
    )


def _lark_runner(timeout: int) -> CommandRunner:
    return _runner(
        os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli"),
        timeout,
    )


def _where_filters(values: list[str]) -> list[dict[str, Any]]:
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


def _reject_aeolus_options(args: argparse.Namespace) -> None:
    if args.report_id or args.where or args.filter_mode != "dashboard":
        raise SourceCaptureError(
            "SOURCE_ARGUMENT_INVALID",
            "--report-id / --where / --filter-mode 仅用于 source_type=aeolus",
        )


class MeegoOperations:
    source_type = "meego"
    operation_arguments = {
        "auth-status": (HOST, TIMEOUT_AUTH),
        "inspect": (URL, PROJECT_KEY, VIEW_ID, FIELD, TIMEOUT_READ),
        "capture": (URL, PROJECT_KEY, VIEW_ID, FIELD, KB, SOURCE_UID, MAX_ITEMS, OUT, BUNDLE_OUT, TIMEOUT_READ),
    }
    runtime_requirements = {name: ("meego",) for name in operation_arguments}

    def run(self, args: argparse.Namespace, *, skill_root: Path) -> dict[str, Any]:
        runner = _meego_runner(args.timeout)
        if args.operation == "auth-status":
            return meego_auth_status(runner=runner, host=args.host)
        _reject_aeolus_options(args)
        if args.operation == "inspect":
            return inspect_meego(
                runner=runner,
                url=args.url,
                project_key=args.project_key,
                view_id=args.view_id,
                fields=args.field,
            )
        if args.source_uid:
            return self._capture_profile(args, runner=runner)
        if args.kb:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--kb 仅和 --source-uid 一起使用",
            )
        return capture_meego(
            runner=runner,
            url=args.url,
            project_key=args.project_key,
            view_id=args.view_id,
            fields=args.field,
            max_items=args.max_items,
        )

    def _capture_profile(
        self,
        args: argparse.Namespace,
        *,
        runner: CommandRunner,
    ) -> dict[str, Any]:
        if not args.kb:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--source-uid 必须同时提供 --kb",
            )
        if (
            args.url
            or args.project_key
            or args.base_token
            or args.table_id
            or args.view_id
            or args.field
            or args.max_items != DEFAULT_MAX_ITEMS
        ):
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "按 --source-uid 抓取时不得用 CLI 覆盖 URL、坐标、字段或行数",
                hint="需要改变口径时保存新的 source profile revision。",
            )
        profile = load_profile(Path(args.kb).expanduser(), args.source_uid)
        if profile["source_type"] != self.source_type:
            raise SourceCaptureError(
                "SOURCE_PROFILE_IDENTITY_MISMATCH",
                "source profile 的 source_type 与 --source-type 不一致",
            )
        selector = profile["selector"]
        policy = profile["capture_policy"]
        result = capture_meego(
            runner=runner,
            url=profile["source_url"],
            project_key=selector["project_key"],
            view_id=selector["view_id"],
            fields=policy["fields"],
            max_items=policy["max_items"],
        )
        if result["source_uid"] != profile["source_uid"]:
            raise SourceCaptureError(
                "SOURCE_PROFILE_IDENTITY_MISMATCH",
                "capture 结果与 source profile 的 source_uid 不一致",
            )
        result["title"] = profile["title"]
        result["source_profile"] = {
            "path": str(profile_relative_path(profile)),
            "revision": profile_revision(profile),
        }
        return result


class BaseOperations:
    source_type = "feishu_base"
    operation_arguments = {
        "auth-status": (TIMEOUT_AUTH,),
        "inspect": (URL, BASE_TOKEN, TABLE_ID, VIEW_ID, TIMEOUT_READ),
        "capture": (URL, BASE_TOKEN, TABLE_ID, VIEW_ID, FIELD, KB, SOURCE_UID, MAX_ITEMS, OUT, BUNDLE_OUT, TIMEOUT_READ),
    }
    runtime_requirements = {name: ("feishu",) for name in operation_arguments}

    def run(self, args: argparse.Namespace, *, skill_root: Path) -> dict[str, Any]:
        runner = _lark_runner(args.timeout)
        if args.operation == "auth-status":
            return base_auth_status(runner=runner)
        _reject_aeolus_options(args)
        if args.operation == "inspect":
            return inspect_base(
                runner=runner,
                url=args.url,
                base_token=args.base_token,
                table_id=args.table_id,
                view_id=args.view_id,
            )
        if args.source_uid:
            return self._capture_profile(args, runner=runner)
        if args.kb:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--kb 仅和 --source-uid 一起使用",
            )
        return capture_base(
            runner=runner,
            url=args.url,
            base_token=args.base_token,
            table_id=args.table_id,
            view_id=args.view_id,
            fields=args.field,
            max_items=args.max_items,
        )

    def _capture_profile(
        self,
        args: argparse.Namespace,
        *,
        runner: CommandRunner,
    ) -> dict[str, Any]:
        if not args.kb:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "--source-uid 必须同时提供 --kb",
            )
        if (
            args.url
            or args.project_key
            or args.base_token
            or args.table_id
            or args.view_id
            or args.field
            or args.max_items != DEFAULT_MAX_ITEMS
        ):
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "按 --source-uid 抓取时不得用 CLI 覆盖 URL、坐标、字段或行数",
                hint="需要改变口径时保存新的 source profile revision。",
            )
        profile = load_profile(Path(args.kb).expanduser(), args.source_uid)
        if profile["source_type"] != self.source_type:
            raise SourceCaptureError(
                "SOURCE_PROFILE_IDENTITY_MISMATCH",
                "source profile 的 source_type 与 --source-type 不一致",
            )
        selector = profile["selector"]
        policy = profile["capture_policy"]
        result = capture_base(
            runner=runner,
            url=profile["source_url"],
            base_token=selector["app_token"],
            table_id=selector["table_id"],
            view_id=selector["view_id"],
            fields=policy["fields"],
            max_items=policy["max_records"],
            page_size=policy["page_size"],
        )
        if result["source_uid"] != profile["source_uid"]:
            raise SourceCaptureError(
                "SOURCE_PROFILE_IDENTITY_MISMATCH",
                "capture 结果与 source profile 的 source_uid 不一致",
            )
        result["title"] = profile["title"]
        result["source_profile"] = {
            "path": str(profile_relative_path(profile)),
            "revision": profile_revision(profile),
        }
        return result


class AeolusOperations:
    source_type = "aeolus"
    operation_arguments = {
        "auth-status": (TIMEOUT_AUTH,),
        "inspect": (URL, REPORT_ID, FILTER_MODE, WHERE, TIMEOUT_READ),
        "capture": (URL, REPORT_ID, FILTER_MODE, WHERE, KB, SOURCE_UID, MAX_ITEMS, OUT, BUNDLE_OUT, TIMEOUT_READ),
        "register": (REGISTER_URL, REPORT_ID, FILTER_MODE, WHERE, REGISTER_KB, MAX_ITEMS, ROUTINE, TIMEOUT_READ),
    }
    runtime_requirements: dict[str, tuple[str, ...]] = {}

    def run(self, args: argparse.Namespace, *, skill_root: Path) -> dict[str, Any]:
        client = aeolus_client_from_environment(timeout_seconds=args.timeout)
        if args.operation == "auth-status":
            return aeolus_auth_status(client=client)
        if args.operation == "register":
            return self._register(args, client=client, skill_root=skill_root)
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
            return self._capture_profile(args, client=client)
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

    def _register(
        self,
        args: argparse.Namespace,
        *,
        client: Any,
        skill_root: Path,
    ) -> dict[str, Any]:
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
            skill_root=skill_root,
        )
        return {**receipt, "profile": profile}

    def _capture_profile(
        self,
        args: argparse.Namespace,
        *,
        client: Any,
    ) -> dict[str, Any]:
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
        profile = load_profile(Path(args.kb).expanduser(), args.source_uid)
        result = capture_aeolus_from_profile(client=client, profile=profile)
        result["source_profile"]["path"] = str(profile_relative_path(profile))
        return result


_ADAPTERS: dict[str, SourceOperationAdapter] = {
    adapter.source_type: adapter
    for adapter in (
        MeegoOperations(),
        BaseOperations(),
        AeolusOperations(),
        FeishuChatOperations(),
    )
}


def source_operation_types() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


def operation_source_types(operation: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            source_type
            for source_type, adapter in _ADAPTERS.items()
            if operation in adapter.operation_arguments
        )
    )


def source_operation_arguments(operation: str) -> tuple[OperationArgument, ...]:
    by_destination: dict[str, OperationArgument] = {}
    for adapter in _ADAPTERS.values():
        for spec in adapter.operation_arguments.get(operation, ()):
            current = by_destination.get(spec.destination)
            if current is not None and current != spec:
                raise RuntimeError(
                    f"source operation argument conflict: {operation}.{spec.destination}"
                )
            by_destination[spec.destination] = spec
    return tuple(by_destination.values())


def source_operation_runtime(source_type: str, operation: str) -> tuple[str, ...]:
    adapter = _ADAPTERS.get(source_type)
    if adapter is None:
        return ()
    return adapter.runtime_requirements.get(operation, ())


def source_operation_manifest() -> dict[str, Any]:
    return {
        operation: {
            "source_types": list(operation_source_types(operation)),
            "arguments": [spec.to_dict() for spec in source_operation_arguments(operation)],
            "runtime": {
                source_type: list(source_operation_runtime(source_type, operation))
                for source_type in operation_source_types(operation)
            },
        }
        for operation in ("auth-status", "inspect", "capture", "register")
    }


def run_source_operation(
    args: argparse.Namespace,
    *,
    skill_root: Path,
) -> dict[str, Any]:
    try:
        adapter = _ADAPTERS[args.source_type]
    except KeyError as exc:
        raise SourceCaptureError(
            "SOURCE_TYPE_UNSUPPORTED",
            f"不支持 source_type={args.source_type}",
        ) from exc
    return adapter.run(args, skill_root=skill_root)
