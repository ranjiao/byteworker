#!/usr/bin/env python3
"""Removed Inbox command tombstone with no business side effects."""

from __future__ import annotations

import json
import sys


def main() -> int:
    json.dump(
        {
            "error": {
                "code": "INBOX_REMOVED",
                "message": "独立 Inbox 已移除；IM 分析已迁移到 Dreaming。",
                "hint": (
                    "使用 `byteworker dreaming process once --source im ...` "
                    "显式扫描，或使用 `byteworker dreaming review` 查看 Finding。"
                ),
            }
        },
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
