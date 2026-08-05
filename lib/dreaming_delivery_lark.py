"""Lark bot delivery adapter for Dreaming report summaries."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dreaming_reports import complete_delivery
from dreaming_state import DreamingError, load_state_unlocked, state_lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _artifact_path(kb: Path, item: Mapping[str, Any]) -> Path:
    kind = str(item.get("kind", ""))
    period = str(item.get("period", ""))
    path = (
        kb.resolve()
        / "state"
        / "dreaming"
        / "reports"
        / f"{kind}-{period}"
        / "artifacts"
        / "summary.txt"
    ).resolve()
    root = (kb.resolve() / "state" / "dreaming").resolve()
    if root not in path.parents or not path.is_file() or path.is_symlink():
        raise DreamingError(
            "DREAMING_DELIVERY_ARTIFACT_MISSING",
            "晨报摘要产物不存在，无法投递。",
        )
    return path


def deliver_lark_bot_summary(
    kb: Path,
    *,
    outbox_id: str,
    binary: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    with state_lock(kb):
        state = load_state_unlocked(kb, current)
        item = state.setdefault("outbox", {}).get(outbox_id)
        if not isinstance(item, Mapping):
            raise DreamingError("DREAMING_DELIVERY_NOT_FOUND", "outbox 不存在。")
        if item.get("status") == "delivered":
            return {
                "outbox_id": outbox_id,
                "status": "delivered",
                "delivery_id": str(item.get("delivery_id", "")),
            }
        if (
            item.get("status") != "pending"
            or item.get("channel") != "lark_bot"
            or item.get("artifact") != "summary"
        ):
            raise DreamingError(
                "DREAMING_DELIVERY_INVALID",
                "outbox 不是待发送的飞书摘要。",
            )
        recipient_id = str(item.get("recipient_id", ""))
        if not recipient_id.startswith("ou_"):
            raise DreamingError(
                "DREAMING_DELIVERY_INVALID",
                "飞书摘要缺少有效收件人。",
            )
        summary_path = _artifact_path(kb, item)
    summary = summary_path.read_text(encoding="utf-8").strip()
    executable = binary or os.environ.get("BYTEWORKER_LARK_CLI_BIN", "lark-cli")
    environment = dict(os.environ)
    environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    try:
        completed = subprocess.run(
            [
                executable,
                "im",
                "+messages-send",
                "--as",
                "bot",
                "--user-id",
                recipient_id,
                "--text",
                summary,
                "--idempotency-key",
                outbox_id,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=60,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise DreamingError(
            "DREAMING_DELIVERY_RUNTIME_MISSING",
            "找不到飞书消息运行时。",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DreamingError(
            "DREAMING_DELIVERY_TIMEOUT",
            "飞书摘要发送超时。",
        ) from exc
    raw = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DreamingError(
            "DREAMING_DELIVERY_FAILED",
            "飞书消息运行时未返回合法 JSON。",
        ) from exc
    if completed.returncode != 0 or not isinstance(response, Mapping) or not response.get("ok"):
        error = response.get("error") if isinstance(response, Mapping) else {}
        code = error.get("code") if isinstance(error, Mapping) else None
        raise DreamingError(
            "DREAMING_DELIVERY_FAILED",
            "飞书晨报摘要发送失败。",
            details={"provider_code": code} if code is not None else {},
        )
    data = response.get("data")
    delivery_id = str(data.get("message_id", "")) if isinstance(data, Mapping) else ""
    if not delivery_id:
        raise DreamingError(
            "DREAMING_DELIVERY_FAILED",
            "飞书消息响应缺少 message_id。",
        )
    complete_delivery(
        kb,
        outbox_id=outbox_id,
        delivery_id=delivery_id,
        now=current,
    )
    return {
        "outbox_id": outbox_id,
        "status": "delivered",
        "channel": "lark_bot",
        "delivery_id": delivery_id,
    }
