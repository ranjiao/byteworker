#!/usr/bin/env python3
"""Inspect Byteworker capabilities and manage low-frequency suggestions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from capability_discovery import (  # noqa: E402
    CapabilityDiscoveryError,
    VALID_SIGNALS,
    capability_ids,
    complete,
    diagnose,
    feedback,
    status,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pretty", action="store_true")
    sub = result.add_subparsers(dest="operation", required=True)

    show = sub.add_parser("status")
    show.add_argument("--kb", required=True, type=Path)

    for operation in ("complete", "recommend", "diagnose"):
        suggestion = sub.add_parser(operation)
        suggestion.add_argument("--kb", required=True, type=Path)
        suggestion.add_argument(
            "--after", required=operation != "diagnose", default="",
            choices=(*capability_ids(), "update", "context"),
        )
        suggestion.add_argument(
            "--signal", action="append", default=[], choices=sorted(VALID_SIGNALS)
        )
        suggestion.add_argument(
            "--outcome", choices=("success", "failed", "needs_input"), default="success"
        )
        interaction = suggestion.add_mutually_exclusive_group()
        interaction.add_argument("--interactive", action="store_true")
        interaction.add_argument("--unattended", action="store_true")

    response = sub.add_parser("feedback")
    response.add_argument("--kb", required=True, type=Path)
    response.add_argument(
        "--action",
        required=True,
        choices=("shown", "dismiss", "snooze", "used", "enable", "disable"),
    )
    response.add_argument("--capability", choices=capability_ids(), default="")
    response.add_argument("--days", type=int, default=30)
    return result


def _validate_kb(value: Path) -> Path:
    kb = value.expanduser().resolve()
    if not kb.is_dir():
        raise CapabilityDiscoveryError(
            "CAPABILITY_DISCOVERY_KB_INVALID", f"知识库目录不存在: {kb}"
        )
    if kb == ROOT or ROOT in kb.parents:
        raise CapabilityDiscoveryError(
            "CAPABILITY_DISCOVERY_KB_INVALID",
            "能力发现状态不得写入 byteworker skill 仓库。",
        )
    return kb


def _run(args: argparse.Namespace) -> object:
    kb = _validate_kb(args.kb)
    if args.operation == "status":
        return status(kb)
    if args.operation in {"complete", "recommend", "diagnose"}:
        action = diagnose if args.operation == "diagnose" else complete
        return action(
            kb, after=args.after, signals=args.signal,
            interactive=args.interactive, outcome=args.outcome,
        )
    if args.operation == "feedback":
        return feedback(
            kb,
            action=args.action,
            capability_id=args.capability,
            days=args.days,
        )
    raise AssertionError(args.operation)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = _run(args)
    except CapabilityDiscoveryError as exc:
        value = {"error": exc.as_dict()}
        code = 2
    else:
        code = 0
    json.dump(
        value,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if args.pretty else None,
    )
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
