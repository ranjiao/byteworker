"""Resolve locally declared input capacity for the active Digest host."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DIGEST_CAPACITY_ROLES = frozenset({
    "digest", "digest_dependency_worker", "digest_semantic_worker", "digest_conflict_worker",
    "digest_final_reducer", "large_digest_worker", "wiki_resume_page",
})


class DigestCapacityError(RuntimeError):
    code = "DIGEST_CAPACITY_CONFIG_INVALID"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def load_digest_capacity() -> dict[str, Any]:
    host = os.environ.get("BYTEWORKER_DIGEST_HOST") or ("codex" if os.environ.get("CODEX_THREAD_ID") else "")
    if not host:
        return {}
    override = os.environ.get("BYTEWORKER_DIGEST_CAPACITY_CONFIG")
    path = Path(override or "~/.config/byteworker/digest-capacity.json").expanduser()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if not override:
            return {}
        raise DigestCapacityError(f"Digest 宿主容量配置不存在：{path}") from exc
    except (OSError, ValueError) as exc:
        raise DigestCapacityError(f"无法读取 Digest 宿主容量配置：{path}") from exc
    hosts = config.get("hosts") if isinstance(config, dict) else None
    if not isinstance(hosts, dict):
        raise DigestCapacityError(f"Digest 宿主容量配置必须有 hosts 对象：{path}")
    limits = hosts.get(host, {})
    if not isinstance(limits, dict) or any(
        (role != "default" and role not in DIGEST_CAPACITY_ROLES) or type(limit) is not int or limit <= 0
        for role, limit in limits.items()
    ):
        raise DigestCapacityError(f"Digest 宿主容量必须按有效角色填写正整数 token 数：{path}")
    default = limits.get("default")
    resolved = {role: limits.get(role, default) for role in sorted(DIGEST_CAPACITY_ROLES) if role in limits or default}
    return {"host": host, "input_tokens": resolved} if resolved else {}
