#!/usr/bin/env python3
"""CLI for deterministic digest analysis preprocessing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_analysis import DigestAnalysisError, prepare_analysis_packet  # noqa: E402
from digest_run_log import DigestRunError, record_stage  # noqa: E402


def configured_kb() -> str:
    config = ROOT / ".kbconfig"
    if not config.is_file():
        return ""
    return config.read_text(encoding="utf-8").splitlines()[0].strip()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="build one private, compact semantic packet from a SourceBundle"
    )
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--bundle", required=True, type=Path)
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--kb", default=configured_kb())
    prepare.add_argument(
        "--run-id",
        default="",
        help="optional digest-run id; automatically records analysis_prepare",
    )
    return result


def _record_failed(kb: Path, run_id: str) -> None:
    try:
        record_stage(
            kb,
            run_id=run_id,
            stage="analysis_prepare",
            status="failed",
            detail_code="DIGEST_ANALYSIS_PREPARE_FAILED",
        )
    except Exception:
        pass


def main() -> int:
    args = parser().parse_args()
    stage_started = False
    kb = Path(args.kb) if args.kb else None
    try:
        if args.run_id:
            if kb is None:
                raise DigestAnalysisError(
                    "KB_CONFIG_MISSING",
                    "--run-id requires --kb or a configured .kbconfig.",
                )
            record_stage(
                kb,
                run_id=args.run_id,
                stage="analysis_prepare",
                status="started",
                detail_code="DIGEST_ANALYSIS_PREPARE_STARTED",
            )
            stage_started = True
        output = prepare_analysis_packet(args.bundle, args.out)
        if args.run_id:
            try:
                record_stage(
                    kb,
                    run_id=args.run_id,
                    stage="analysis_prepare",
                    status="completed",
                    detail_code="DIGEST_ANALYSIS_PREPARE_COMPLETED",
                    metrics={
                        "component_count": output["component_count"],
                        "input_bytes": output["input_bytes"],
                        "output_count": 1,
                    },
                )
                output["digest_run_id"] = args.run_id
                output["digest_run_logging"] = "ok"
            except Exception as exc:
                code = (
                    exc.code
                    if isinstance(exc, DigestRunError)
                    else "DIGEST_RUN_LOG_IO_ERROR"
                )
                output["digest_run_id"] = args.run_id
                output["digest_run_logging"] = "degraded"
                output["warnings"] = [
                    "digest-run completion logging degraded: " + code
                ]
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except DigestAnalysisError as exc:
        if stage_started:
            _record_failed(kb, args.run_id)
        print(
            json.dumps(
                {"status": "error", "error": exc.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except DigestRunError as exc:
        print(
            json.dumps(
                {"status": "error", "error": exc.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except Exception as exc:
        if stage_started:
            _record_failed(kb, args.run_id)
        error = DigestAnalysisError("DIGEST_ANALYSIS_ERROR", f"unexpected: {exc}")
        print(
            json.dumps(
                {"status": "error", "error": error.as_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
