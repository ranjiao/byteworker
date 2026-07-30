#!/usr/bin/env python3
"""Stable launcher for Byteworker preflight, runtime tools, and machine CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from runtime_deps import check_runtime, render_dependency_report, runtime_environment  # noqa: E402


def _exec(argv: list[str], env: dict[str, str]) -> int:
    try:
        os.execvpe(argv[0], argv, env)
    except OSError as exc:
        print(f"byteworker: 无法执行 {argv[0]}: {exc}", file=sys.stderr)
        return 1


def _runtime_command(name: str, args: list[str]) -> int:
    result = check_runtime(required_sources={name}, include_optional=False)
    if not result["ready"]:
        print(render_dependency_report(result), file=sys.stderr)
        return 1
    executable_key = "lark-cli" if name == "feishu" else "meegle"
    executable = result["programs"][executable_key]["path"]
    return _exec([executable, *args], runtime_environment(result))


def _required_sources(values: list[str]) -> set[str]:
    if values[:1] == ["wiki"]:
        operation = values[1] if len(values) > 1 else ""
        return {"feishu"} if operation in {"auth-status", "inspect", "scan"} else set()
    if values[:1] != ["source"]:
        return set()
    operation = values[1] if len(values) > 1 else ""
    if operation not in {"auth-status", "inspect", "capture"}:
        return set()
    source_type = ""
    for index, value in enumerate(values):
        if value.startswith("--source-type="):
            source_type = value.split("=", 1)[1]
            break
        if value == "--source-type" and index + 1 < len(values):
            source_type = values[index + 1]
            break
    if not source_type:
        return set()
    if source_type.startswith(("feishu_", "lark_")):
        return {"feishu"}
    if source_type in {"meego", "meegle"}:
        return {"meego"}
    return set()


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    if not values:
        values = ["--help"]
    command, *rest = values

    if command == "preflight":
        return _exec(
            [
                os.environ.get("BYTEWORKER_PYTHON_BIN", sys.executable),
                str(ROOT / "bin" / "session-preflight.py"),
                *rest,
            ],
            dict(os.environ),
        )
    if command == "deps":
        result = check_runtime(
            required_sources={"feishu", "meego"},
            include_optional=True,
        )
        print(render_dependency_report(result))
        if any(
            item["required"] and item["tier"] == 1 and item["status"] != "ok"
            for item in result["programs"].values()
        ):
            return 1
        return 2 if not result["ready"] else 0
    if command == "lark":
        return _runtime_command("feishu", rest)
    if command == "meegle":
        return _runtime_command("meego", rest)
    if command == "run":
        if not rest:
            print("byteworker: run 需要命令参数。", file=sys.stderr)
            return 2
        result = check_runtime(include_optional=True)
        if not result["ready"]:
            print(render_dependency_report(result), file=sys.stderr)
            return 1
        return _exec(rest, runtime_environment(result))

    required_sources = _required_sources(values)
    result = check_runtime(
        required_sources=required_sources,
        include_optional=False,
    )
    if not result["ready"]:
        print(render_dependency_report(result), file=sys.stderr)
        return 1
    return _exec(
        [
            os.environ.get("BYTEWORKER_PYTHON_BIN", sys.executable),
            str(ROOT / "bin" / "byteworker-cli.py"),
            *values,
        ],
        runtime_environment(result),
    )


if __name__ == "__main__":
    raise SystemExit(main())
