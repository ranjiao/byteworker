#!/usr/bin/env python3
"""Machine-readable facade over byteworker's deterministic helper CLIs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from command_registry import (  # noqa: E402
    attention_exit_codes,
    command_spec,
    facade_entrypoints,
    resolve_operation,
)
from bounded_process import run_bounded_output  # noqa: E402
from machine_protocol import context, envelope, error_payload, write_envelope  # noqa: E402
from update_state import load_state  # noqa: E402


TOOLS = facade_entrypoints()
ATTENTION_EXIT_CODES = attention_exit_codes()


class ProtocolUsageError(ValueError):
    pass


class ProtocolArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ProtocolUsageError(message)


def parser() -> ProtocolArgumentParser:
    result = ProtocolArgumentParser(
        description="byteworker deterministic CLI with a stable JSON envelope"
    )
    result.add_argument("--pretty", action="store_true", help="缩进 JSON 输出")
    sub = result.add_subparsers(dest="tool", required=True, metavar="<command>")
    for name in TOOLS:
        spec = command_spec(name)
        command = sub.add_parser(
            name,
            add_help=False,
            help=spec.summary if spec is not None else "",
        )
        command.add_argument("args", nargs=argparse.REMAINDER)
    update = command_spec("update-status")
    sub.add_parser(
        "update-status",
        help=update.summary if update is not None else "",
    )
    return result


def _operation(tool: str, args: list[str]) -> str:
    spec = command_spec(tool)
    return resolve_operation(spec, args) if spec is not None else ""


def _bounded(value: str, limit: int = 2048) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _parse_json(value: str) -> Any:
    if not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip()


def _legacy_error_message(data: Any, stderr: str) -> str:
    if isinstance(data, dict):
        value = data.get("error") or data.get("message")
        if isinstance(value, dict):
            value = value.get("message") or value.get("error")
        if isinstance(value, str) and value.strip():
            return _bounded(value)
    if stderr.strip():
        return _bounded(stderr)
    if isinstance(data, str) and data.strip():
        return _bounded(data)
    return "命令执行失败"


def _error_code(tool: str, returncode: int, message: str) -> tuple[str, str]:
    if ".kbconfig" in message or "未指定 --kb" in message:
        return "KB_CONFIG_MISSING", "先完成知识库初始化，或显式传入 --kb。"
    prefix = tool.upper().replace("-", "_")
    suffix = "INPUT_ERROR" if returncode == 2 else "ERROR"
    return f"{prefix}_{suffix}", ""


def _structured_error(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict) or not isinstance(data.get("error"), dict):
        return None
    value = data["error"]
    code = str(value.get("code", "")).strip()
    message = str(value.get("message", "")).strip()
    if not code or not message:
        return None
    return error_payload(
        code=code,
        message=_bounded(message),
        hint=_bounded(str(value.get("hint", ""))),
        details=value.get("details"),
    )


def _tool_help_request(values: list[str]) -> tuple[str, list[str]] | None:
    candidates = values[1:] if values[:1] == ["--pretty"] else values
    if (
        len(candidates) >= 2
        and candidates[0] in TOOLS
        and any(value in {"-h", "--help"} for value in candidates[1:])
    ):
        return candidates[0], candidates[1:]
    return None


def _run_tool_help(tool: str, args: list[str] | None = None) -> int:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "bin" / TOOLS[tool]), *(args or ["--help"])],
        check=False,
    )
    return completed.returncode


def _run_tool(tool: str, args: list[str], *, pretty: bool) -> int:
    start = time.monotonic()
    forwarded = list(args)
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    if tool == "doctor" and not any(
        item == "--format" or item.startswith("--format=") for item in forwarded
    ):
        forwarded.extend(["--format", "json"])
    completed = run_bounded_output(
        [sys.executable, str(ROOT / "bin" / TOOLS[tool]), *forwarded]
    )
    elapsed = round((time.monotonic() - start) * 1000)
    data = completed.artifact or _parse_json(completed.stdout)
    spec = command_spec(tool)
    operation = _operation(tool, forwarded)
    context_value = context(
        tool=tool,
        operation=operation,
        execution_time_ms=elapsed,
        command_path=f"{tool}.{operation}" if operation else tool,
        stability=spec.stability if spec is not None else "",
        side_effect=spec.side_effect if spec is not None else "",
    )
    if completed.returncode == 0:
        payload = envelope(
            status="success",
            data=data,
            error=None,
            context_value=context_value,
        )
    elif completed.returncode in ATTENTION_EXIT_CODES.get(tool, set()) and not isinstance(
        data, str
    ):
        payload = envelope(
            status="attention",
            data=data,
            error=None,
            context_value=context_value,
        )
    else:
        message = _legacy_error_message(data, completed.stderr)
        code, hint = _error_code(tool, completed.returncode, message)
        details: dict[str, Any] = {"exit_code": completed.returncode}
        if completed.stderr.strip():
            details["stderr"] = _bounded(completed.stderr)
        if completed.artifact is not None:
            details["output_artifact"] = completed.artifact
        structured = _structured_error(data)
        payload = envelope(
            status="error",
            data=None,
            error=structured
            or error_payload(
                code=code,
                message=message,
                hint=hint,
                details=details,
            ),
            context_value=context_value,
        )
    write_envelope(sys.stdout, payload, pretty=pretty)
    return completed.returncode


def _update_status(*, pretty: bool) -> int:
    start = time.monotonic()
    data = load_state(ROOT / ".update-state.json", ROOT / ".last-update-check")
    elapsed = round((time.monotonic() - start) * 1000)
    spec = command_spec("update-status")
    payload = envelope(
        status="success",
        data=data,
        error=None,
        context_value=context(
            tool="update-status",
            operation="get",
            execution_time_ms=elapsed,
            command_path="update-status.get",
            stability=spec.stability if spec is not None else "",
            side_effect=spec.side_effect if spec is not None else "",
        ),
    )
    write_envelope(sys.stdout, payload, pretty=pretty)
    return 0


def main(argv: list[str] | None = None) -> int:
    values = sys.argv[1:] if argv is None else argv
    help_tool = _tool_help_request(values)
    if help_tool is not None:
        tool, help_args = help_tool
        return _run_tool_help(tool, help_args)
    try:
        args = parser().parse_args(values)
    except ProtocolUsageError as exc:
        payload = envelope(
            status="error",
            data=None,
            error=error_payload(
                code="CLI_USAGE_ERROR",
                message=_bounded(str(exc)),
                hint="运行 byteworker-cli.py --help 查看可用工具。",
                details={"exit_code": 2},
            ),
            context_value=context(
                tool="cli",
                operation="parse",
                execution_time_ms=0,
            ),
        )
        write_envelope(sys.stdout, payload, pretty="--pretty" in values)
        return 2
    if args.tool == "update-status":
        return _update_status(pretty=args.pretty)
    return _run_tool(args.tool, args.args, pretty=args.pretty)


if __name__ == "__main__":
    raise SystemExit(main())
