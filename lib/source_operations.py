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


class SourceOperationAdapter(Protocol):
    source_type: str

    def run(self, args: argparse.Namespace, *, skill_root: Path) -> dict[str, Any]:
        ...


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
            raise SourceCaptureError(
                "SOURCE_PROFILE_UNSUPPORTED",
                "当前 profile capture 暂不支持 source_type=feishu_base",
            )
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


class AeolusOperations:
    source_type = "aeolus"

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
    for adapter in (MeegoOperations(), BaseOperations(), AeolusOperations())
}


def source_operation_types() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


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
