#!/usr/bin/env python3
"""Validate bounded semantic outputs before durable writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from semantic_policy import (  # noqa: E402
    SemanticPolicyError,
    validate_im_semantic_file,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Validate semantic result JSON")
    result.add_argument("command", choices=("validate-im",))
    result.add_argument("--input", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        output = validate_im_semantic_file(args.input)
    except SemanticPolicyError as exc:
        print(
            json.dumps(
                {"error": {"code": exc.code, "message": str(exc)}},
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {"status": "valid", "threads": len(output["threads"])},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
