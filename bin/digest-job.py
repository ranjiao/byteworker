#!/usr/bin/env python3
"""Manage persistent, bounded Wiki digest jobs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_jobs import (  # noqa: E402
    DigestJobError,
    cancel_job,
    create_job,
    job_status,
    lease_next,
    list_jobs,
    mark_page,
    reconcile_job,
)


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--pretty", action="store_true")
    sub = result.add_subparsers(dest="operation", required=True)

    create = sub.add_parser("create")
    create.add_argument("--kb", required=True, type=Path)
    create.add_argument("--selection", required=True, type=Path)
    create.add_argument("--title", default="")
    create.add_argument("--organization-node-id", default="")
    create.add_argument("--batch-size", type=_positive, default=5)

    listing = sub.add_parser("list")
    listing.add_argument("--kb", required=True, type=Path)
    listing.add_argument("--active", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("--kb", required=True, type=Path)
    status.add_argument("--job-id", required=True)
    status.add_argument("--limit", type=_positive, default=20)

    next_batch = sub.add_parser("next")
    next_batch.add_argument("--kb", required=True, type=Path)
    next_batch.add_argument("--job-id", required=True)
    next_batch.add_argument("--limit", type=_positive, default=5)
    next_batch.add_argument("--lease-owner", required=True)
    next_batch.add_argument("--lease-seconds", type=_positive, default=1800)

    mark = sub.add_parser("mark")
    mark.add_argument("--kb", required=True, type=Path)
    mark.add_argument("--job-id", required=True)
    mark.add_argument("--document-id", required=True)
    mark.add_argument(
        "--status",
        required=True,
        choices=(
            "noop",
            "committed",
            "blocked_dependency",
            "blocked_conflict",
            "retryable_error",
            "permanent_error",
            "skipped",
        ),
    )
    mark.add_argument("--raw-id", default="")
    mark.add_argument("--commit", default="")
    mark.add_argument("--error", default="")

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--kb", required=True, type=Path)
    reconcile.add_argument("--job-id", required=True)

    cancel = sub.add_parser("cancel")
    cancel.add_argument("--kb", required=True, type=Path)
    cancel.add_argument("--job-id", required=True)
    return result


def _read_selection(path: Path) -> dict:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise DigestJobError(
            "DIGEST_JOB_SELECTION_INVALID",
            "候选列表不得位于 byteworker skill 仓库。",
        )
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DigestJobError(
            "DIGEST_JOB_SELECTION_INVALID",
            f"无法读取候选列表: {path}",
        ) from exc
    if not isinstance(value, dict):
        raise DigestJobError(
            "DIGEST_JOB_SELECTION_INVALID",
            "候选列表顶层必须是 JSON 对象。",
        )
    return value


def _validate_kb(kb: Path) -> Path:
    resolved = kb.expanduser().resolve()
    if not resolved.is_dir():
        raise DigestJobError(
            "DIGEST_JOB_KB_INVALID",
            f"知识库目录不存在: {resolved}",
        )
    if resolved == ROOT or ROOT in resolved.parents:
        raise DigestJobError(
            "DIGEST_JOB_KB_INVALID",
            "digest job 不得写入 byteworker skill 仓库。",
        )
    return resolved


def _run(args: argparse.Namespace) -> object:
    kb = _validate_kb(args.kb)
    if args.operation == "create":
        return create_job(
            kb,
            _read_selection(args.selection),
            title=args.title,
            organization_node_id=args.organization_node_id,
            batch_size=args.batch_size,
        )
    if args.operation == "list":
        return {"jobs": list_jobs(kb, active_only=args.active)}
    if args.operation == "status":
        return job_status(kb, args.job_id, limit=args.limit)
    if args.operation == "next":
        return lease_next(
            kb,
            args.job_id,
            limit=args.limit,
            lease_owner=args.lease_owner,
            lease_seconds=args.lease_seconds,
        )
    if args.operation == "mark":
        return mark_page(
            kb,
            args.job_id,
            document_id=args.document_id,
            status=args.status,
            raw_id=args.raw_id,
            commit=args.commit,
            error=args.error,
        )
    if args.operation == "reconcile":
        return reconcile_job(kb, args.job_id)
    if args.operation == "cancel":
        return cancel_job(kb, args.job_id)
    raise AssertionError(args.operation)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value = _run(args)
        json.dump(
            value,
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
        sys.stdout.write("\n")
    except DigestJobError as exc:
        json.dump(
            {"error": exc.as_dict()},
            sys.stdout,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
        sys.stdout.write("\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
