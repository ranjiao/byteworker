#!/usr/bin/env python3
"""Audit, plan, validate, and apply byteworker provenance backfills."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


SELF_DIR = Path(__file__).resolve().parent
ROOT = SELF_DIR.parent
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from provenance_backfill import (  # noqa: E402
    BackfillError,
    apply_backfill,
    audit_kb,
    build_plan,
    validate_backfill,
    validation_report,
)


def configured_kb() -> str:
    config = ROOT / ".kbconfig"
    if not config.is_file():
        return ""
    return config.read_text(encoding="utf-8").splitlines()[0].strip()


def inside_skill(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def allowed_business_artifact(path: Path, kb: Path) -> bool:
    return inside(path, kb) or inside(path, Path(tempfile.gettempdir()))


def reject_skill_candidates(plan_path: Path) -> None:
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for node in plan.get("nodes", []):
        if not isinstance(node, dict):
            continue
        candidate = str(node.get("candidate", "")).strip()
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = plan_path.parent / path
        if inside_skill(path):
            raise BackfillError(
                "候选节点可能含业务数据，不能从 skill 仓库读取"
            )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="byteworker 历史 raw 出处与事实证据回填工具",
    )
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("audit", "plan", "validate", "apply"):
        command = sub.add_parser(name)
        command.add_argument(
            "--kb",
            default=configured_kb(),
            help="知识库数据目录；默认读取 .kbconfig",
        )
        if name == "plan":
            command.add_argument(
                "--output",
                required=True,
                help="候选计划输出路径；必须位于系统临时目录或知识库目录",
            )
        if name in {"validate", "apply"}:
            command.add_argument(
                "--plan",
                required=True,
                help="经 Agent / 用户检查的 backfill plan",
            )
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.kb:
        print("错误: 未指定 --kb 且 .kbconfig 不存在", file=sys.stderr)
        return 2
    kb = Path(args.kb)
    try:
        if args.command == "audit":
            output = audit_kb(kb)
        elif args.command == "plan":
            target = Path(args.output).resolve()
            if inside_skill(target):
                raise BackfillError(
                    "backfill plan 含业务 raw_id/节点路径，不能写入 skill 仓库"
                )
            if not allowed_business_artifact(target, kb):
                raise BackfillError(
                    "backfill plan 只能写入系统临时目录或知识库目录"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            plan = build_plan(kb)
            target.write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            output = {
                "status": "planned",
                "plan": str(target),
                "raw_candidates": len(plan["raws"]),
                "node_candidates": len(plan["nodes"]),
                "applied": False,
            }
        else:
            plan_path = Path(args.plan).resolve()
            if inside_skill(plan_path):
                raise BackfillError(
                    "backfill plan 可能含业务数据，不能放在 skill 仓库"
                )
            if not allowed_business_artifact(plan_path, kb):
                raise BackfillError(
                    "backfill plan 只能从系统临时目录或知识库目录读取"
                )
            reject_skill_candidates(plan_path)
            if args.command == "validate":
                output = validation_report(
                    validate_backfill(kb, plan_path),
                    kb,
                )
            else:
                output = apply_backfill(kb, plan_path)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (BackfillError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"unexpected: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
