"""Resolve a user-facing Lark username to a delivery-safe open_id."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Mapping

from dreaming_state import DreamingError


_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,49}$")


def _identity_keys(user: Mapping[str, Any]) -> set[str]:
    keys = {
        str(user.get(field, "")).strip().casefold()
        for field in ("user_id", "username", "employee_id", "feishu_id")
    }
    for field in ("enterprise_email", "email"):
        email = str(user.get(field, "")).strip()
        if "@" in email:
            keys.add(email.split("@", 1)[0].casefold())
    keys.discard("")
    return keys


def _candidate_summary(user: Mapping[str, Any]) -> dict[str, str]:
    return {
        "name": str(user.get("localized_name") or user.get("name") or ""),
        "enterprise_email": str(user.get("enterprise_email") or ""),
        "department": str(user.get("department") or ""),
    }


def resolve_lark_recipient(
    recipient: str,
    *,
    binary: str | None = None,
) -> dict[str, str]:
    """Resolve an open_id or exact username into persisted delivery identity."""

    value = recipient.strip()
    if not value:
        return {"recipient_id": "", "recipient_key": "", "display_name": ""}
    if value.startswith("ou_"):
        return {
            "recipient_id": value,
            "recipient_key": value,
            "display_name": "",
        }
    if not _USERNAME_RE.fullmatch(value):
        raise DreamingError(
            "DREAMING_RECIPIENT_INVALID",
            "飞书收件人请填写字母用户名，例如 ranjiao；也兼容 ou_ 开头的 open_id。",
        )

    executable = binary or os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli")
    environment = dict(os.environ)
    environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    try:
        completed = subprocess.run(
            [
                executable,
                "contact",
                "+search-user",
                "--query",
                value,
                "--exclude-external-users",
                "--as",
                "user",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DreamingError(
            "DREAMING_RECIPIENT_LOOKUP_FAILED",
            "无法查询飞书收件人，请检查 lark-cli 登录和通讯录权限。",
        ) from exc
    if completed.returncode != 0:
        raise DreamingError(
            "DREAMING_RECIPIENT_LOOKUP_FAILED",
            "无法查询飞书收件人，请检查 lark-cli 登录和通讯录权限。",
            details={"exit_code": completed.returncode},
        )
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DreamingError(
            "DREAMING_RECIPIENT_LOOKUP_FAILED",
            "飞书通讯录返回了无法解析的结果。",
        ) from exc
    data = payload.get("data") if isinstance(payload, Mapping) else None
    raw_users = data.get("users") if isinstance(data, Mapping) else None
    users = [item for item in raw_users or [] if isinstance(item, Mapping)]
    by_open_id = {
        str(item.get("open_id", "")).strip(): item
        for item in users
        if str(item.get("open_id", "")).startswith("ou_")
    }
    normalized = value.casefold()
    exact = {
        open_id: user
        for open_id, user in by_open_id.items()
        if normalized in _identity_keys(user)
    }
    matches = exact or (
        by_open_id
        if len(by_open_id) == 1 and not bool(data.get("has_more"))
        else {}
    )
    if len(matches) != 1:
        code = (
            "DREAMING_RECIPIENT_NOT_FOUND"
            if not by_open_id
            else "DREAMING_RECIPIENT_AMBIGUOUS"
        )
        message = (
            "没有找到这个飞书用户名。"
            if not by_open_id
            else "这个用户名匹配到多位用户，请填写更完整的用户名或直接填写 open_id。"
        )
        raise DreamingError(
            code,
            message,
            details={
                "query": value,
                "candidates": [
                    _candidate_summary(item) for item in list(by_open_id.values())[:5]
                ],
            },
        )
    open_id, user = next(iter(matches.items()))
    return {
        "recipient_id": open_id,
        "recipient_key": value,
        "display_name": str(
            user.get("localized_name") or user.get("name") or ""
        ).strip(),
    }
