"""One-shot, quiet startup checks shared by every Byteworker session."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from report_automation import record_decision, status as report_status
from runtime_deps import check_runtime, runtime_environment


FEISHU_SOURCE_PREFIXES = ("feishu_", "lark_")


def configured_requirements(kb: Path) -> set[str]:
    required: set[str] = set()
    sources = kb / "sources"
    if not sources.is_dir():
        return required
    for path in sources.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = value.get("source")
        source_type = str(
            value.get("source_type")
            or (source.get("source_type") if isinstance(source, Mapping) else "")
            or ""
        ).strip()
        if source_type.startswith(FEISHU_SOURCE_PREFIXES):
            required.add("feishu")
        elif source_type in {"meego", "meegle"}:
            required.add("meego")
    return required


def _notice(code: str, message: str, *, severity: str = "notice", data: Any = None) -> dict[str, Any]:
    result = {"code": code, "severity": severity, "message": message}
    if data is not None:
        result["data"] = data
    return result


def _todo_notice_item(value: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in (
        "id",
        "title",
        "category",
        "due_at",
        "remind_at",
        "snoozed_until",
    ):
        item = value.get(key)
        if isinstance(item, str) and item:
            result[key] = item[:240]
    return result


def _run_json(
    argv: list[str],
    *,
    env: Mapping[str, str],
    runner=subprocess.run,
) -> tuple[int, Any, str]:
    try:
        completed = runner(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
            env=dict(env),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, None, " ".join(str(exc).split())[:500]
    try:
        data = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    error = " ".join((completed.stderr or completed.stdout).split())[:500]
    return completed.returncode, data, error


def run_preflight(
    root: Path,
    *,
    kb_override: Path | None = None,
    required_sources: Iterable[str] = (),
    skip_update: bool = False,
    environ: Mapping[str, str] | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    notices: list[dict[str, Any]] = []
    blocking = False
    config = root / ".kbconfig"
    kb: Path | None = kb_override.resolve() if kb_override else None
    kb_notice: dict[str, Any] | None = None
    if kb is None:
        try:
            config_value = (
                config.read_text(encoding="utf-8").strip()
                if config.is_file()
                else ""
            )
        except OSError as exc:
            config_value = ""
            kb_notice = _notice(
                "KB_CONFIG_INVALID",
                "无法读取知识库配置: " + str(exc)[:300],
                severity="blocking",
            )
        if kb_notice is None and not config_value:
            kb_notice = _notice(
                "KB_CONFIG_MISSING",
                "这是第一次使用 Byteworker；先完成知识库目录设置或上手引导。",
                severity="blocking",
            )
        elif kb_notice is None:
            kb = Path(
                config_value.splitlines()[0].strip()
            ).expanduser().resolve()
    if kb is not None and not kb.is_dir():
        kb_notice = _notice(
            "KB_DIRECTORY_MISSING",
            f"知识库目录不存在: {kb}",
            severity="blocking",
        )

    requirements = set(required_sources)
    if kb is not None and kb.is_dir():
        requirements |= configured_requirements(kb)
    runtime = check_runtime(
        required_sources=requirements,
        include_optional=False,
        environ=environ,
    )
    env = runtime_environment(runtime, environ=environ)
    if not runtime["ready"]:
        blocking = True
        missing = [
            name
            for name, item in runtime["programs"].items()
            if item["required"] and item["status"] != "ok"
        ]
        if runtime["python"]["status"] != "ok":
            missing.insert(0, "python")
        notices.append(
            _notice(
                "RUNTIME_DEPENDENCY_INVALID",
                "运行依赖未就绪: " + ", ".join(missing),
                severity="blocking",
                data={"runtime": runtime},
            )
        )

    if not skip_update and runtime["core_ready"]:
        try:
            completed = runner(
                [str(root / "bin" / "update-check.sh")],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=env,
            )
        except OSError as exc:
            update_message = "自动更新检查无法启动: " + str(exc)[:500]
        else:
            update_message = " ".join(
                (completed.stdout or completed.stderr).split()
            )[:1000]
            if completed.returncode != 0 and not update_message:
                update_message = (
                    f"自动更新检查失败（退出码 {completed.returncode}）。"
                )
        if update_message:
            notices.append(_notice("UPDATE_CHECK_NOTICE", update_message))

    if kb_notice is not None:
        notices.append(kb_notice)
        return {
            "schema_version": "byteworker-session-preflight/v1",
            "status": "attention",
            "ready": False,
            "kb": str(kb or ""),
            "context_path": "",
            "required_sources": sorted(requirements),
            "runtime": runtime,
            "notices": notices,
        }
    assert kb is not None
    context_path = kb / "context.md"
    if not context_path.is_file():
        blocking = True
        notices.append(
            _notice(
                "CONTEXT_MISSING",
                "知识库缺少 context.md，需要先按模板初始化。",
                severity="blocking",
            )
        )

    todo_path = kb / "todo.md"
    if not todo_path.is_file():
        blocking = True
        notices.append(
            _notice(
                "TODO_MISSING",
                "知识库缺少 todo.md，需要先按模板初始化并创建本地回滚点。",
                severity="blocking",
            )
        )
    elif runtime["core_ready"]:
        code, todo_data, error = _run_json(
            [
                runtime["python"]["path"],
                str(root / "bin" / "todo.py"),
                str(kb),
                "check",
            ],
            env=env,
            runner=runner,
        )
        if code != 0 or not isinstance(todo_data, list):
            notices.append(
                _notice(
                    "TODO_CHECK_FAILED",
                    "Todo 检查失败: " + (error or "返回格式无效"),
                    severity="blocking",
                )
            )
            blocking = True
        elif todo_data:
            notices.append(
                _notice(
                    "TODO_REMINDERS",
                    f"有 {len(todo_data)} 项 Todo 到期、逾期或临期。",
                    data={
                        "items": [
                            _todo_notice_item(item)
                            for item in todo_data[:3]
                            if isinstance(item, Mapping)
                        ],
                        "total": len(todo_data),
                    },
                )
            )

    try:
        report = report_status(kb)
    except Exception as exc:  # bounded boundary: corrupt local state is actionable
        notices.append(
            _notice(
                "REPORT_AUTOMATION_STATE_INVALID",
                "自动报告状态检查失败: " + str(exc)[:300],
                severity="blocking",
            )
        )
        blocking = True
    else:
        if report.get("needs_onboarding"):
            try:
                record_decision(kb, decision="prompted")
            except Exception as exc:
                notices.append(
                    _notice(
                        "REPORT_AUTOMATION_STATE_INVALID",
                        "无法记录自动报告引导状态: " + str(exc)[:300],
                        severity="blocking",
                    )
                )
                blocking = True
            else:
                notices.append(
                    _notice(
                        "REPORT_AUTOMATION_ONBOARDING",
                        "自动日报、周报和补偿检查尚未设置；完成当前请求后询问用户是否配置。",
                    )
                )
        elif report.get("prompt_upgrade_available"):
            notices.append(
                _notice(
                    "REPORT_AUTOMATION_PROMPT_UPGRADE",
                    "宿主自动报告 prompt 版本落后；完成当前请求后询问用户是否更新。",
                )
            )

    return {
        "schema_version": "byteworker-session-preflight/v1",
        "status": "attention" if notices else "healthy",
        "ready": not blocking,
        "kb": str(kb),
        "context_path": str(context_path),
        "required_sources": sorted(requirements),
        "runtime": runtime,
        "notices": notices,
    }
