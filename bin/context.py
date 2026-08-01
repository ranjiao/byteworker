#!/usr/bin/env python3
"""Read an intent-scoped context.md projection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from context_view import ContextViewError, INTENT_SECTIONS, context_view  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Read an intent-scoped context view")
    result.add_argument("command", choices=("view",))
    result.add_argument("--kb", type=Path, required=True)
    result.add_argument("--intent", choices=sorted(INTENT_SECTIONS), required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        output = context_view(args.kb, args.intent)
    except ContextViewError as exc:
        print(
            json.dumps(
                {"error": {"code": exc.code, "message": str(exc)}},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
