"""Profile-driven Feishu chat operation adapter.

The existing shell script remains the pagination/high-water transport.  This
module owns the Python operation boundary and converts its completed artifacts
to a validated SourceBundle.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from source_capture import CommandRunner, DEFAULT_MAX_ITEMS, SourceCaptureError
from source_profiles import (
    load_profile,
    profile_relative_path,
    profile_revision,
)
from sources import create_default_registry


def _lark_runner(timeout: int) -> CommandRunner:
    return CommandRunner(
        os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli"),
        timeout_seconds=timeout,
    )


def _auth_status(runner: CommandRunner) -> dict[str, Any]:
    """Check the shared Lark user identity without claiming provider scopes."""

    response = runner.run_status(
        ["auth", "status", "--verify"],
        provider="飞书",
    ).data
    if not isinstance(response, dict):
        raise SourceCaptureError(
            "SOURCE_INVALID_RESPONSE",
            "lark-cli auth status 返回的不是对象",
        )
    identities = response.get("identities")
    user = (
        identities.get("user")
        if isinstance(identities, dict)
        and isinstance(identities.get("user"), dict)
        else {}
    )
    authenticated = bool(
        user.get("available") is True
        and str(user.get("status", "")).lower() == "ready"
        and str(user.get("tokenStatus", "")).lower() == "valid"
    )
    verified = bool(
        user.get("verified") is True
        or (
            str(response.get("identity", "")).lower() == "user"
            and response.get("verified") is True
        )
    )
    ready = authenticated and verified
    return {
        "schema_version": "byteworker-source-auth/v1",
        "source_type": "feishu_chat",
        "identity": "user",
        "authenticated": authenticated,
        "verified": verified,
        "authorized": None,
        "ready": ready,
        "required_scopes": [],
        "missing_scopes": [],
        "scope_verification": "capture-time",
        "action": None
        if ready
        else {
            "kind": "login",
            "command": "lark-cli auth login --no-wait --json",
            "interactive": True,
            "requires_qr": True,
            "message": "先完成 lark-cli 用户登录；群聊只读权限会在 capture 时校验。",
        },
    }


def _summary(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip():
            result[key.strip()] = value.strip()
    required = {
        "chat_id",
        "messages",
        "pages",
        "truncated",
        "window",
        "mode",
        "transcript",
        "locators",
    }
    missing = sorted(required - set(result))
    if missing:
        raise SourceCaptureError(
            "SOURCE_INVALID_RESPONSE",
            "pull-chat.sh 摘要缺少字段: " + ", ".join(missing),
        )
    return result


class FeishuChatOperations:
    source_type = "feishu_chat"

    def run(self, args: argparse.Namespace, *, skill_root: Path) -> dict[str, Any]:
        if args.operation == "auth-status":
            return _auth_status(_lark_runner(args.timeout))
        if args.operation == "inspect":
            raise SourceCaptureError(
                "SOURCE_OPERATION_UNSUPPORTED",
                "feishu_chat 不提供独立 inspect；首次先用 pull-chat 定位 chat_id，"
                "再保存 Profile。",
            )
        self._validate_capture_args(args)
        profile = load_profile(Path(args.kb).expanduser(), args.source_uid)
        if profile["source_type"] != self.source_type:
            raise SourceCaptureError(
                "SOURCE_PROFILE_IDENTITY_MISMATCH",
                "source profile 的 source_type 与 --source-type 不一致",
            )
        output = Path(args.out).expanduser().resolve()
        root = skill_root.resolve()
        if output == root or root in output.parents:
            raise SourceCaptureError(
                "SOURCE_OUTPUT_IN_SKILL_REPO",
                "群聊 bundle 与逐字稿不得写入 byteworker skill 仓库",
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        artifact_root = Path(
            tempfile.mkdtemp(
                prefix=f".{output.stem}-components-",
                dir=output.parent,
            )
        )
        transcript = artifact_root / "transcript.txt"
        locators = artifact_root / "locators.json"
        policy = profile["capture_policy"]
        command = self._command(
            args,
            skill_root=skill_root,
            profile=profile,
            transcript=transcript,
            locators=locators,
        )
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=args.timeout,
                check=False,
                env={**os.environ},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            shutil.rmtree(artifact_root, ignore_errors=True)
            raise SourceCaptureError(
                "SOURCE_CHAT_CAPTURE_FAILED",
                f"无法执行 pull-chat.sh: {exc}",
            ) from exc
        if completed.returncode != 0:
            shutil.rmtree(artifact_root, ignore_errors=True)
            raise SourceCaptureError(
                "SOURCE_CHAT_CAPTURE_FAILED",
                "pull-chat.sh 未完成完整群聊窗口抓取",
                details={
                    "exit_code": completed.returncode,
                    "stderr": completed.stderr[-1200:],
                },
            )
        try:
            summary = _summary(completed.stdout)
            if summary["chat_id"] != profile["source_uid"]:
                raise SourceCaptureError(
                    "SOURCE_PROFILE_IDENTITY_MISMATCH",
                    "群聊 capture 结果与 source profile 的 chat_id 不一致",
                )
            bundle = create_default_registry().build_bundle(
                "feishu_chat",
                source_uid=profile["source_uid"],
                source_url=profile["source_url"],
                title=profile["title"],
                revision=summary["window"],
                source_window=summary["window"],
                transcript={"path": str(transcript)},
                locator_artifact=locators,
                provider_metadata={
                    "message_count": int(summary["messages"]),
                    "page_count": int(summary["pages"]),
                    "capture_mode": summary["mode"],
                    "page_size": policy["page_size"],
                    "overlap_seconds": policy["overlap_seconds"],
                    "source_profile": {
                        "path": str(profile_relative_path(profile)),
                        "revision": profile_revision(profile),
                    },
                },
                skill_root=skill_root,
            )
        except Exception:
            shutil.rmtree(artifact_root, ignore_errors=True)
            raise
        return bundle.to_dict()

    @staticmethod
    def _validate_capture_args(args: argparse.Namespace) -> None:
        if not args.source_uid or not args.kb:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "feishu_chat capture 必须同时提供 --kb 与 --source-uid",
                hint="首次窗口仍可直接运行 bin/pull-chat.sh，确认后保存 v2 Profile。",
            )
        if not args.out:
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "feishu_chat capture 必须提供 --out 保存 SourceBundle。",
            )
        if (
            args.url
            or args.project_key
            or args.base_token
            or args.table_id
            or args.view_id
            or args.field
            or args.report_id
            or args.where
            or args.filter_mode != "dashboard"
            or args.max_items != DEFAULT_MAX_ITEMS
        ):
            raise SourceCaptureError(
                "SOURCE_ARGUMENT_INVALID",
                "按 feishu_chat Profile 抓取时不得用 CLI 覆盖来源窗口或 selector。",
            )

    @staticmethod
    def _command(
        args: argparse.Namespace,
        *,
        skill_root: Path,
        profile: dict[str, Any],
        transcript: Path,
        locators: Path,
    ) -> list[str]:
        policy = profile["capture_policy"]
        command = [
            str(skill_root / "bin" / "pull-chat.sh"),
            "--chat-id",
            profile["selector"]["chat_id"],
            "--kb",
            str(Path(args.kb).expanduser().resolve()),
            "--out",
            str(transcript),
            "--locators-out",
            str(locators),
            "--page-size",
            str(policy["page_size"]),
            "--overlap-seconds",
            str(policy["overlap_seconds"]),
        ]
        if policy["since_last"]:
            command.append("--since-last")
            if policy["end"]:
                command.extend(["--end", policy["end"]])
        else:
            command.extend(
                ["--start", policy["start"], "--end", policy["end"]]
            )
        return command
