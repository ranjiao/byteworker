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

from doctor import render_text, scan  # noqa: E402
from digest_feedback import export_bundle, feedback_draft  # noqa: E402
from digest_run_log import DigestRunError  # noqa: E402
import operation_trace as trace  # noqa: E402
from support_feedback import generate  # noqa: E402
from update_postflight import run_postflight  # noqa: E402


def resolve_kb(value: str) -> Path:
    if value:
        kb = Path(value).expanduser()
    else:
        config = SKILL_ROOT / ".kbconfig"
        lines = config.read_text(encoding="utf-8").splitlines() if config.is_file() else []
        if not lines or not lines[0].strip():
            raise ValueError("未找到 .kbconfig；请用 --kb 指定知识库目录")
        kb = Path(lines[0].strip()).expanduser()
    if not kb.is_dir():
        raise ValueError(f"知识库目录不存在: {kb}")
    return kb.resolve()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="检查知识库兼容性，或从本地技术回执生成反馈草稿和诊断包。"
    )
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("scan", "fix", "feedback", "bundle", "trace-start", "trace-finish", "trace-list"):
        command = sub.add_parser(name)
        command.add_argument("--kb", default="", help="知识库目录；默认读取 .kbconfig")
        command.add_argument("--format", choices=("text", "json"), default="text",
                             help="报告格式" if name in {"scan", "fix"} else argparse.SUPPRESS)
        if name in {"feedback", "bundle", "trace-finish"}:
            command.add_argument("--run-id", required=name == "trace-finish", help="运行或诊断编号")
        if name in {"trace-start", "trace-list"}:
            command.add_argument("--kind", choices=sorted(trace.KINDS - {"dreaming"}), required=name == "trace-start")
        if name == "trace-finish":
            command.add_argument("--result", required=True, choices=(
                "success", "failed", "partial", "insufficient_evidence", "incorrect_result", "no_match",
            ))
        if name != "fix":
            continue
        command.add_argument(
            "--only", default="index,links", help="fix 范围，逗号分隔:index,links",
        )
        command.add_argument("--autolink", action="store_true", help="把正文提及的已存在节点补进 links")
        command.add_argument("--dry-run", action="store_true", help="预演 fix，不写回")
    return result


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or (values[0].startswith("-") and not set(values) & {"-h", "--help"}):
        values.insert(0, "scan")
    args = parser().parse_args(values)
    if args.command in {"feedback", "bundle", "trace-start", "trace-finish", "trace-list"}:
        try:
            kb = Path(args.kb).expanduser().resolve() if args.kb else None
            if args.command == "trace-start":
                data = {"run_id": trace.start(args.kind, kb), "kind": args.kind}
            elif args.command == "trace-list":
                data = {"traces": [
                    {"run_id": item["run_id"], **item["events"][-1]}
                    for item in trace.recent(kind=args.kind, kb=kb) if item["events"]
                ]}
            elif not args.run_id:
                raise DigestRunError("DIGEST_FEEDBACK_RUN_REQUIRED", "请指定 --run-id。")
            elif args.command == "trace-finish":
                if not args.run_id.startswith("BW-") or args.result is None:
                    raise ValueError("trace-finish 需要 BW 诊断编号和 --result。")
                trace.record(args.run_id, "agent_result", args.result)
                data = {"run_id": args.run_id, "status": args.result}
            elif args.run_id.startswith(("BW-", "DT-", "DR-")):
                if kb is None and not args.run_id.startswith("BW-"):
                    kb = resolve_kb("")
                data = generate(
                    kb, run_id=args.run_id, root=SKILL_ROOT, bundle=args.command == "bundle",
                )
            else:
                action = feedback_draft if args.command == "feedback" else export_bundle
                data = action(resolve_kb(args.kb), run_id=args.run_id, skill_root=SKILL_ROOT)
        except DigestRunError as exc:
            print(json.dumps({"error": exc.as_dict()}, ensure_ascii=False))
            return 1
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps(data, ensure_ascii=False))
        return 0

    try:
        kb = resolve_kb(args.kb)
        repairs = []
        transaction = None
        if args.command == "fix":
            actions = [item.strip() for item in args.only.split(",") if item.strip()]
            transaction = run_postflight(
                SKILL_ROOT,
                kb,
                allowed_actions=set(actions),
                force_autolink=args.autolink,
                dry_run=args.dry_run,
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

    if transaction is not None and transaction.execution_failed:
        return 1
    summary = report.summary()
    return 2 if summary["error"] or summary["warning"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
