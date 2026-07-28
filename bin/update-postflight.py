#!/usr/bin/env python3
"""Run the byteworker KB doctor after a successful skill update."""

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

from update_postflight import render_message, run_postflight  # noqa: E402


def brief_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= 180 else text[:179] + "…"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="skill 更新成功后静默检查并修复知识库兼容问题。"
    )
    result.add_argument("--kb", default="", help="知识库目录；默认读取 .kbconfig")
    result.add_argument("--format", choices=("text", "json"), default="text")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.kb:
        kb = Path(args.kb).expanduser()
    else:
        config = SKILL_ROOT / ".kbconfig"
        if not config.is_file():
            return 0
        lines = config.read_text(encoding="utf-8").splitlines()
        if not lines or not lines[0].strip():
            return 0
        kb = Path(lines[0].strip()).expanduser()
    if not kb.is_dir():
        message = "doctor:知识库目录不存在，更新后兼容检查未完成。请决定是否立即检查。"
        print(
            json.dumps(
                {"status": "decision", "message": message},
                ensure_ascii=False,
            )
            if args.format == "json"
            else message
        )
        return 2
    try:
        result = run_postflight(SKILL_ROOT, kb)
    except Exception as exc:  # update hook must return a bounded actionable message
        message = (
            f"doctor:更新后兼容检查执行失败({brief_error(exc)})。"
            "请决定是否立即检查。"
        )
        print(
            json.dumps(
                {"status": "decision", "message": message},
                ensure_ascii=False,
            )
            if args.format == "json"
            else message
        )
        return 2
    message = render_message(result)
    if args.format == "json":
        payload = result.to_dict()
        payload["message"] = message
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(message)
    return 2 if result.status == "decision" else 0


if __name__ == "__main__":
    raise SystemExit(main())
