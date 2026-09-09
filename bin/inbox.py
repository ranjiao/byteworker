#!/usr/bin/env python3
"""Removed Inbox command tombstone with no business side effects."""

from __future__ import annotations

import argparse
import json
import sys


def parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="已移除的 Inbox 兼容入口；IM 分析已迁移到 Dreaming。"
    )


def main(argv: list[str] | None = None) -> int:
    parser().parse_known_args(argv)
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
