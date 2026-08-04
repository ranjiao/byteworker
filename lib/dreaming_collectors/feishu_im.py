"""User-identity Feishu IM collector for Dreaming."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from dreaming_state import DreamingError


Command = Callable[[list[str]], dict[str, Any]]


def _default_command(timeout: int) -> Command:
    binary = os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli")

    def run(args: list[str]) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [binary, *args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DreamingError(
                "DREAMING_IM_CAPTURE_FAILED",
                f"无法执行 lark-cli: {exc}",
            ) from exc
        stream = completed.stdout if completed.stdout.strip() else completed.stderr
        try:
            value = json.loads(stream)
        except json.JSONDecodeError as exc:
            raise DreamingError(
                "DREAMING_IM_CAPTURE_FAILED",
                "lark-cli 未返回合法 JSON。",
            ) from exc
        auth_status = (
            args[:2] == ["auth", "status"]
            and isinstance(value, dict)
            and isinstance(value.get("identities"), Mapping)
        )
        if (
            completed.returncode != 0
            or not isinstance(value, dict)
            or (value.get("ok") is not True and not auth_status)
        ):
            raw_error = value.get("error") if isinstance(value, dict) else None
            error = (
                dict(raw_error)
                if isinstance(raw_error, Mapping)
                else {
                    "message": (
                        completed.stderr.strip()
                        or str(raw_error or "lark-cli command failed")
                    )
                }
            )
            code = (
                "SOURCE_AUTH_REQUIRED"
                if error.get("type") == "authorization"
                else "DREAMING_IM_CAPTURE_FAILED"
            )
            raise DreamingError(code, "lark-cli IM 读取失败。", details={"error": error})
        return value

    return run


def _messages(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = value.get("data")
    if not isinstance(data, Mapping):
        raise DreamingError(
            "DREAMING_IM_INVALID_RESPONSE",
            "lark-cli IM 响应缺少 data。",
        )
    raw = data.get("messages", [])
    if not isinstance(raw, list):
        raise DreamingError(
            "DREAMING_IM_INVALID_RESPONSE",
            "lark-cli IM messages 不是数组。",
        )
    result = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        message_id = str(item.get("message_id", "")).strip()
        chat_id = str(item.get("chat_id", "")).strip()
        if not message_id or not chat_id:
            continue
        content = item.get("content", "")
        if not isinstance(content, (str, dict, list)):
            content = str(content)
        sender = item.get("sender")
        sender = dict(sender) if isinstance(sender, Mapping) else {}
        result.append(
            {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": str(item.get("chat_type", "unknown")),
                "chat_name": str(item.get("chat_name", "")),
                "create_time": str(item.get("create_time", "")),
                "update_time": str(item.get("update_time", "")),
                "msg_type": str(item.get("msg_type", "unknown")),
                "thread_id": str(item.get("thread_id", "")),
                "sender": sender,
                "content": content,
                "mentions": item.get("mentions", []),
                "reactions": item.get("reactions", {}),
            }
        )
    return result


class FeishuImCollector:
    def __init__(
        self,
        *,
        timeout: int = 60,
        page_size: int = 50,
        max_messages: int = 3000,
        command: Command | None = None,
    ) -> None:
        self.page_size = max(1, min(50, page_size))
        self.max_messages = max(1, max_messages)
        self.command = command or _default_command(timeout)

    def _paginate(self, base: list[str]) -> tuple[list[dict[str, Any]], bool]:
        result: list[dict[str, Any]] = []
        token = ""
        truncated = False
        while True:
            args = [*base, "--page-size", str(self.page_size), "--format", "json"]
            if token:
                args.extend(["--page-token", token])
            response = self.command(args)
            result.extend(_messages(response))
            data = response["data"]
            has_more = bool(data.get("has_more"))
            token = str(data.get("page_token", ""))
            if len(result) >= self.max_messages:
                truncated = has_more or len(result) > self.max_messages
                result = result[: self.max_messages]
                break
            if not has_more or not token:
                break
        return result, truncated

    def principal(self) -> str:
        response = self.command(["auth", "status", "--verify", "--json"])
        data = response.get("data")
        data = data if isinstance(data, Mapping) else response
        identities = data.get("identities")
        user = identities.get("user") if isinstance(identities, Mapping) else {}
        open_id = (
            user.get("openId")
            if isinstance(user, Mapping)
            else ""
        )
        if not isinstance(open_id, str) or not open_id.strip():
            raise DreamingError(
                "SOURCE_AUTH_REQUIRED",
                "无法确认 lark-cli 当前用户 open_id。",
            )
        return f"user:{open_id.strip()}"

    def collect_monitored(
        self,
        *,
        chat_ids: list[str],
        start: str,
        end: str,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        truncated_chats = []
        for chat_id in chat_ids:
            remaining = self.max_messages - len(messages)
            if remaining <= 0:
                truncated_chats.append(chat_id)
                break
            previous_limit = self.max_messages
            self.max_messages = remaining
            try:
                page, truncated = self._paginate(
                    [
                        "im",
                        "+chat-messages-list",
                        "--as",
                        "user",
                        "--chat-id",
                        chat_id,
                        "--start",
                        start,
                        "--end",
                        end,
                        "--order",
                        "asc",
                        "--no-reactions",
                    ]
                )
            finally:
                self.max_messages = previous_limit
            messages.extend(page)
            if truncated:
                truncated_chats.append(chat_id)
        return {
            "lane": "monitored",
            "messages": messages,
            "coverage": {
                "status": "partial" if truncated_chats else "complete",
                "gaps": [
                    {"kind": "chat_truncated", "chat_id": chat_id}
                    for chat_id in truncated_chats
                ],
            },
        }

    def collect_discovery(self, *, start: str, end: str) -> dict[str, Any]:
        messages, truncated = self._paginate(
            [
                "im",
                "+messages-search",
                "--as",
                "user",
                "--query",
                "",
                "--start",
                start,
                "--end",
                end,
                "--no-reactions",
            ]
        )
        gaps = []
        if truncated:
            gaps.append(
                {
                    "kind": "window_budget",
                    "start": start,
                    "end": end,
                    "resume": "split_time_window",
                }
            )
        return {
            "lane": "discovery",
            "messages": messages,
            "coverage": {"status": "best_effort", "gaps": gaps},
        }
