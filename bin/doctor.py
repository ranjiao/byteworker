#!/usr/bin/env python3
"""Scan a byteworker KB for schema drift and apply bounded deterministic fixes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SELF_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SELF_DIR.parent
LIB_DIR = SKILL_ROOT / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from doctor import apply_repairs, render_text, scan  # noqa: E402
from update_postflight import run_postflight  # noqa: E402


def resolve_kb(value: str) -> Path:
    if value:
        kb = Path(value).expanduser()
    else:
        config = SKILL_ROOT / ".kbconfig"
        if not config.is_file():
            raise ValueError("未找到 .kbconfig；请用 --kb 指定知识库目录")
        kb = Path(config.read_text(encoding="utf-8").splitlines()[0].strip()).expanduser()
    if not kb.is_dir():
        raise ValueError(f"知识库目录不存在: {kb}")
    return kb.resolve()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="扫描 byteworker 知识库与当前 skill/schema 的兼容性。"
    )
    result.add_argument("command", nargs="?", choices=("scan", "fix"), default="scan")
    result.add_argument("--kb", default="", help="知识库目录；默认读取 .kbconfig")
    result.add_argument(
        "--format", choices=("text", "json"), default="text", help="报告格式"
    )
    result.add_argument(
        "--only",
        default="index,links",
        help="fix 范围，逗号分隔:index,links",
    )
    result.add_argument(
        "--autolink",
        action="store_true",
        help="fix links 时把正文提及的已存在节点补进 links",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="预演 fix，不写回",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        kb = resolve_kb(args.kb)
        repairs = []
        transaction = None
        if args.command == "fix":
            actions = [item.strip() for item in args.only.split(",") if item.strip()]
            if args.dry_run:
                repairs = apply_repairs(
                    kb,
                    SKILL_ROOT,
                    actions,
                    autolink=args.autolink,
                    dry_run=True,
                )
            else:
                transaction = run_postflight(
                    SKILL_ROOT,
                    kb,
                    allowed_actions=set(actions),
                    force_autolink=args.autolink,
                )
                repairs = transaction.repairs
        report = scan(kb, SKILL_ROOT)
    except (OSError, ValueError) as exc:
        print(f"doctor 错误: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        payload = report.to_dict()
        if repairs:
            payload["repairs"] = repairs
        if transaction is not None:
            payload["transaction"] = transaction.to_dict()
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        if repairs:
            print("byteworker · doctor repair")
            for item in repairs:
                status = "✓" if item["ok"] else "✗"
                print(
                    f"{status} {item['action']} "
                    f"(mode={item['mode']}, exit={item['exit_code']})"
                )
                detail = item["stdout"] or item["stderr"]
                if detail:
                    for line in detail.splitlines():
                        print("  " + line)
            print()
        sys.stdout.write(render_text(report))

    if any(not item["ok"] for item in repairs):
        return 1
    summary = report.summary()
    return 2 if summary["error"] or summary["warning"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
