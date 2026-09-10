"""Single source of truth for Byteworker command discovery and dispatch."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

from machine_protocol import output_policy


MANIFEST_VERSION = "byteworker-command-manifest/v1"
DESCRIPTION_VERSION = "byteworker-command-description/v1"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    category: str
    execution: str
    audience: str
    visibility: str
    stability: str
    side_effect: str
    output_protocol: str
    docs: str
    entrypoint: str = ""
    operations: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    runtime_operations: tuple[str, ...] = ()
    attention_exit_codes: tuple[int, ...] = ()
    usage: str = ""
    options: tuple[tuple[str, str], ...] = ()

    def to_dict(self, *, operation: str = "") -> dict[str, object]:
        result = asdict(self)
        result["command_path"] = (
            f"{self.name}.{operation}" if operation else self.name
        )
        result["help_command"] = "bin/byteworker " + self.name
        if operation:
            result["help_command"] += " " + operation
        result["help_command"] += " --help"
        target_path = (self.name, *((operation,) if operation else ()))
        preferred = preferred_path_for(target_path)
        result["preferred_path"] = ".".join(preferred)
        result["preferred_help_command"] = (
            "bin/byteworker " + " ".join(preferred) + " --help"
        )
        return result


@dataclass(frozen=True)
class CommandAlias:
    path: tuple[str, ...]
    target: tuple[str, ...]
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": ".".join(self.path),
            "argv": " ".join(self.path),
            "target": ".".join(self.target),
            "summary": self.summary,
            "stability": "alias",
        }


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "preflight", "执行每个 session 一次的启动检查", "system", "launcher",
        "agent", "public", "stable", "state-write", "plain",
        "references/session-preflight.md", entrypoint="session-preflight.py",
        usage="byteworker preflight [--kb PATH] [--require SOURCE] [--interactive|--unattended] [--json]",
    ),
    CommandSpec(
        "deps", "检查或刷新本地运行依赖", "system", "launcher", "user",
        "public", "stable", "state-write", "plain",
        "references/session-preflight.md", usage="byteworker deps [--refresh] [--cache-status]",
        options=(("--refresh", "重新探测 runtime 并刷新缓存"), ("--cache-status", "只显示当前缓存状态")),
    ),
    CommandSpec(
        "runtime-reset", "清除 Python 和 runtime 解析缓存", "system", "launcher",
        "maintainer", "public", "stable", "state-write", "plain",
        "references/session-preflight.md", usage="byteworker runtime-reset",
    ),
    CommandSpec(
        "commands", "列出、描述或搜索 Byteworker 命令", "system", "launcher",
        "agent", "public", "stable", "none", MANIFEST_VERSION,
        "references/machine-protocol.md", operations=("list", "describe", "search"),
        usage="byteworker commands {list,describe,search} [--json]",
    ),
    CommandSpec(
        "discover", "查看用户能力地图并管理低频功能建议", "system", "facade",
        "agent", "public", "stable", "state-write", "byteworker-cli/v1",
        "references/capability-discovery.md", entrypoint="discover.py",
        operations=("status", "recommend", "feedback"),
    ),
    CommandSpec(
        "update-status", "读取自动更新状态", "system", "facade-special", "agent",
        "public", "stable", "none", "byteworker-cli/v1",
        "references/session-preflight.md", operations=("get",),
        usage="byteworker update-status",
    ),
    CommandSpec(
        "source", "来源授权、抓取、Profile、Bundle 与差异入口", "source",
        "facade", "agent", "public", "stable", "mixed", "byteworker-cli/v1",
        "bin/README.md", entrypoint="source.py",
        operations=("capabilities", "bundle-spec", "auth-status", "inspect", "capture", "bundle", "register", "profile", "profile-save", "profiles", "diff"),
        runtime_operations=("auth-status", "inspect", "capture"),
    ),
    CommandSpec(
        "wiki", "按需探索 Wiki 空间并管理候选", "source", "facade", "agent",
        "public", "stable", "mixed", "byteworker-cli/v1",
        "references/digest-wiki-space.md", entrypoint="wiki.py",
        operations=("auth-status", "inspect", "scan", "topics", "candidates", "profile-create"),
        required_sources=("feishu",), runtime_operations=("auth-status", "inspect", "scan"),
    ),
    CommandSpec(
        "digest-flow", "执行可恢复的标准 digest 生命周期", "digest", "facade",
        "agent", "public", "stable", "mixed", "byteworker-cli/v1",
        "references/digest-flow.md", entrypoint="digest-flow.py",
        operations=("start", "capture", "prepare", "commit", "status"),
    ),
    CommandSpec(
        "digest-job", "管理已确认的多页 digest 批次", "digest", "facade",
        "agent", "public", "stable", "state-write", "byteworker-cli/v1",
        "references/wiki-digest-jobs.md", entrypoint="digest-job.py",
        operations=("create", "list", "status", "next", "mark", "reconcile", "cancel"),
    ),
    CommandSpec(
        "digest-run", "记录和查询 digest 阶段、耗时与 usage", "digest", "facade",
        "agent", "advanced", "stable", "state-write", "byteworker-cli/v1",
        "references/digest-observability.md", entrypoint="digest-run.py",
        operations=("start", "stage", "complete", "usage", "wait", "resume", "heartbeat", "list", "show"),
    ),
    CommandSpec(
        "digest-txn", "预检、校验并原子提交 digest", "digest", "facade",
        "agent", "advanced", "stable", "kb-write", "byteworker-cli/v1",
        "references/digest-transaction.md", entrypoint="digest-txn.py",
        operations=("preflight", "validate", "execute", "snapshot-node"),
    ),
    CommandSpec(
        "digest-analysis", "生成一次性 digest 分析 packet", "digest", "facade",
        "agent", "advanced", "stable", "temp-write", "byteworker-cli/v1",
        "references/digest-analysis-pipeline.md", entrypoint="digest-analysis.py",
        operations=("prepare",),
    ),
    CommandSpec(
        "digest-capture", "执行有界且只读的来源抓取计划", "digest", "facade",
        "agent", "advanced", "stable", "temp-write", "byteworker-cli/v1",
        "references/digest-concurrency.md", entrypoint="digest-capture.py",
        operations=("execute",),
    ),
    CommandSpec(
        "digest-parallel", "规划和归并有界 digest workers", "digest", "facade",
        "agent", "advanced", "stable", "temp-write", "byteworker-cli/v1",
        "references/digest-concurrency.md", entrypoint="digest-parallel.py",
        operations=("plan", "merge"),
    ),
    CommandSpec(
        "workflow-budget", "检查完整 workflow token 预算", "digest", "facade",
        "agent", "advanced", "stable", "none", "byteworker-cli/v1",
        "references/workflow-budgets.md", entrypoint="workflow-budget.py",
        operations=("inspect",),
    ),
    CommandSpec(
        "kb-query", "查询节点、证据、冲突和结构化记录", "knowledge",
        "facade", "agent", "public", "stable", "none", "byteworker-cli/v1",
        "references/command-search.md", entrypoint="kb-query.py",
        operations=("search", "conflict-search", "evidence", "source-record"),
    ),
    CommandSpec(
        "kb-mutate", "校验或提交非 digest 的 KB mutation", "knowledge",
        "facade", "agent", "public", "stable", "kb-write", "byteworker-cli/v1",
        "references/kb-mutation.md", entrypoint="kb-mutate.py",
        operations=("validate", "execute"),
    ),
    CommandSpec(
        "context", "读取按意图裁剪的工作上下文", "knowledge", "facade",
        "agent", "public", "stable", "none", "byteworker-cli/v1",
        "references/command-context.md", entrypoint="context.py", operations=("view",),
    ),
    CommandSpec(
        "semantic", "校验结构化语义决定", "knowledge", "facade", "agent",
        "public", "stable", "none", "byteworker-cli/v1",
        "references/semantic-policy.md", entrypoint="semantic.py", operations=("validate-im",),
    ),
    CommandSpec(
        "index", "预演或执行 INDEX 重建", "knowledge", "facade", "agent",
        "public", "stable", "kb-write", "byteworker-cli/v1",
        "references/maintenance.md", entrypoint="index.py", operations=("rebuild",),
    ),
    CommandSpec(
        "doctor", "扫描或修复知识库兼容性问题", "knowledge", "facade",
        "agent", "public", "stable", "mixed", "byteworker-cli/v1",
        "references/doctor.md", entrypoint="doctor.py", operations=("scan", "fix"),
        attention_exit_codes=(2,),
    ),
    CommandSpec(
        "provenance-backfill", "审计和回填历史出处", "knowledge", "facade",
        "maintainer", "advanced", "stable", "kb-write", "byteworker-cli/v1",
        "references/provenance.md", entrypoint="provenance-backfill.py",
        operations=("audit", "plan", "validate", "apply"),
    ),
    CommandSpec(
        "todo", "维护 Todo 状态和提醒时间", "productivity", "facade", "agent",
        "public", "stable", "kb-write", "byteworker-cli/v1",
        "references/todo.md", entrypoint="todo.py",
        operations=("init", "parse-time", "add", "list", "check", "status", "snooze", "mark-reminded", "edit"),
    ),
    CommandSpec(
        "report-automation", "管理自动报告设置、租约和运行回执", "automation",
        "facade", "agent", "public", "stable", "state-write", "byteworker-cli/v1",
        "references/report-scheduling.md", entrypoint="report-automation.py",
        operations=("status", "decision", "configure", "check", "lease", "complete", "release-owner", "restore-owner"),
    ),
    CommandSpec(
        "dreaming", "管理可选的 Dreaming 调度和处理流程", "automation",
        "facade", "agent", "public", "stable", "mixed", "byteworker-cli/v1",
        "references/dreaming.md", entrypoint="dreaming.py",
        operations=("status", "configure", "enable", "disable", "manage-reports", "run-due", "renew", "retry-job", "harness", "heartbeat", "runs", "grant", "process", "review", "explain", "feedback", "shadow", "report", "action", "complete"),
    ),
    CommandSpec(
        "lark", "在统一 runtime 中调用 lark-cli", "external", "external",
        "agent", "public", "stable", "external", "passthrough",
        "references/machine-protocol.md", required_sources=("feishu",),
        usage="byteworker lark <lark-cli arguments...>",
    ),
    CommandSpec(
        "meegle", "在统一 runtime 中调用 Meegle CLI", "external", "external",
        "agent", "public", "stable", "external", "passthrough",
        "references/machine-protocol.md", required_sources=("meego",),
        usage="byteworker meegle <meegle arguments...>",
    ),
    CommandSpec(
        "run", "在统一 runtime 中执行受控 helper", "maintainer", "launcher",
        "maintainer", "advanced", "stable", "arbitrary-exec", "passthrough",
        "references/machine-protocol.md", usage="byteworker run <command> [arguments...]",
    ),
    CommandSpec(
        "inbox", "已移除的 Inbox 兼容入口", "compatibility", "facade",
        "compatibility", "hidden", "tombstone", "none", "byteworker-cli/v1",
        "references/help.md", entrypoint="inbox.py",
    ),
)


COMMAND_ALIASES: tuple[CommandAlias, ...] = (
    CommandAlias(("system", "preflight"), ("preflight",), "执行 session 启动检查"),
    CommandAlias(("system", "deps"), ("deps",), "检查或刷新运行依赖"),
    CommandAlias(("system", "runtime-reset"), ("runtime-reset",), "重置 runtime 缓存"),
    CommandAlias(("system", "update-status"), ("update-status",), "读取更新状态"),
    CommandAlias(("system", "discover"), ("discover",), "查看能力地图和功能建议"),
    CommandAlias(("source", "auth"), ("source", "auth-status"), "检查来源授权"),
    CommandAlias(("source", "wiki"), ("wiki",), "探索 Wiki 空间"),
    CommandAlias(("digest", "flow"), ("digest-flow",), "标准 digest 生命周期"),
    CommandAlias(("digest", "job"), ("digest-job",), "多页 digest 批次"),
    CommandAlias(("digest", "inspect", "run"), ("digest-run",), "查看阶段与 usage"),
    CommandAlias(("digest", "inspect", "analysis"), ("digest-analysis",), "生成分析 packet"),
    CommandAlias(("digest", "internal", "txn"), ("digest-txn",), "底层原子事务"),
    CommandAlias(("digest", "internal", "capture"), ("digest-capture",), "底层抓取计划"),
    CommandAlias(("digest", "internal", "parallel"), ("digest-parallel",), "底层并行规划"),
    CommandAlias(("digest", "internal", "budget"), ("workflow-budget",), "工作流 token 预算"),
    CommandAlias(("kb", "query"), ("kb-query",), "查询节点、证据和记录"),
    CommandAlias(("kb", "mutate"), ("kb-mutate",), "提交非 digest mutation"),
    CommandAlias(("kb", "context"), ("context",), "读取有限上下文"),
    CommandAlias(("kb", "semantic"), ("semantic",), "校验语义决定"),
    CommandAlias(("kb", "index"), ("index",), "重建 INDEX"),
    CommandAlias(("kb", "doctor"), ("doctor",), "扫描或修复 KB"),
    CommandAlias(("kb", "provenance"), ("provenance-backfill",), "审计和回填出处"),
    CommandAlias(("automation", "report"), ("report-automation",), "管理自动报告"),
    CommandAlias(("automation", "dreaming"), ("dreaming",), "管理 Dreaming"),
    CommandAlias(("external", "lark"), ("lark",), "调用 lark-cli"),
    CommandAlias(("external", "meegle"), ("meegle",), "调用 Meegle CLI"),
    CommandAlias(("maintainer", "run"), ("run",), "执行受控 helper"),
)


_BY_NAME = {spec.name: spec for spec in COMMAND_SPECS}
if len(_BY_NAME) != len(COMMAND_SPECS):
    raise RuntimeError("duplicate Byteworker command name")

_ALIAS_BY_PATH = {alias.path: alias for alias in COMMAND_ALIASES}
if len(_ALIAS_BY_PATH) != len(COMMAND_ALIASES):
    raise RuntimeError("duplicate Byteworker command alias")


def command_aliases() -> tuple[CommandAlias, ...]:
    return COMMAND_ALIASES


def namespace_names() -> tuple[str, ...]:
    return tuple(dict.fromkeys(alias.path[0] for alias in COMMAND_ALIASES))


def _matching_alias(values: Sequence[str]) -> CommandAlias | None:
    candidates = [
        alias
        for alias in COMMAND_ALIASES
        if tuple(values[: len(alias.path)]) == alias.path
    ]
    return max(candidates, key=lambda alias: len(alias.path), default=None)


def resolve_command_alias(argv: Sequence[str]) -> list[str]:
    values = list(argv)
    alias = _matching_alias(values)
    if alias is None:
        return values
    return [*alias.target, *values[len(alias.path) :]]


def preferred_path_for(target: Sequence[str]) -> tuple[str, ...]:
    values = tuple(target)
    candidates = [
        alias
        for alias in COMMAND_ALIASES
        if values[: len(alias.target)] == alias.target
    ]
    alias = max(candidates, key=lambda item: len(item.target), default=None)
    if alias is None:
        return values
    return (*alias.path, *values[len(alias.target) :])


def render_namespace_help(prefix: Sequence[str]) -> str | None:
    values = tuple(prefix)
    matches = [
        alias for alias in COMMAND_ALIASES if alias.path[: len(values)] == values
    ]
    if not values or not matches:
        return None
    children: dict[str, list[CommandAlias]] = {}
    for alias in matches:
        if len(alias.path) <= len(values):
            continue
        children.setdefault(alias.path[len(values)], []).append(alias)
    if not children:
        return None
    lines = [f"usage: byteworker {' '.join(values)} <command> [arguments...]", ""]
    lines.append(f"Preferred {'.'.join(values)} namespace.")
    lines.extend(("", "commands:"))
    for child, aliases in children.items():
        exact = next((item for item in aliases if len(item.path) == len(values) + 1), None)
        summary = exact.summary if exact else f"{child} 子命令组"
        lines.append(f"  {child:<20} {summary}")
    lines.extend(("", "旧的扁平命令路径继续兼容。", ""))
    return "\n".join(lines)


def command_specs(*, include_hidden: bool = False) -> tuple[CommandSpec, ...]:
    if include_hidden:
        return COMMAND_SPECS
    return tuple(spec for spec in COMMAND_SPECS if spec.visibility != "hidden")


def command_spec(name: str) -> CommandSpec | None:
    return _BY_NAME.get(name)


def facade_entrypoints() -> dict[str, str]:
    return {
        spec.name: spec.entrypoint
        for spec in COMMAND_SPECS
        if spec.execution == "facade"
    }


def attention_exit_codes() -> dict[str, set[int]]:
    return {
        spec.name: set(spec.attention_exit_codes)
        for spec in COMMAND_SPECS
        if spec.attention_exit_codes
    }


def resolve_operation(spec: CommandSpec, args: Sequence[str]) -> str:
    values = [value for value in args if value != "--"]
    for value in values:
        if value in spec.operations:
            return value
    return ""


def _argument_value(args: Sequence[str], name: str) -> str:
    for index, value in enumerate(args):
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
        if value == name and index + 1 < len(args):
            return args[index + 1]
    return ""


def required_sources_for(argv: Sequence[str]) -> set[str]:
    values = list(argv)
    if values[:1] == ["--pretty"]:
        values = values[1:]
    if not values or any(value in {"-h", "--help"} for value in values):
        return set()
    values = resolve_command_alias(values)
    spec = command_spec(values[0])
    if spec is None:
        return set()
    operation = resolve_operation(spec, values[1:])
    if spec.runtime_operations and operation not in spec.runtime_operations:
        return set()
    if spec.name == "source":
        source_type = _argument_value(values[1:], "--source-type")
        from source_operations import source_operation_runtime

        return set(source_operation_runtime(source_type, operation))
    return set(spec.required_sources)


def command_description(path: str) -> dict[str, object] | None:
    normalized = path.replace("/", ".").replace(" ", ".").strip(".")
    parts = tuple(value for value in normalized.split(".") if value)
    if not parts:
        return None
    resolved = tuple(resolve_command_alias(parts))
    alias = _matching_alias(parts)
    spec = command_spec(resolved[0])
    if spec is None:
        return None
    operation = resolved[1] if len(resolved) > 1 else ""
    if operation and operation not in spec.operations:
        return None
    result = spec.to_dict(operation=operation)
    if len(resolved) > 2:
        result["nested_path"] = list(resolved[1:])
    if alias is not None:
        result["alias_target"] = ".".join(resolved)
        result["command_path"] = ".".join(parts)
        result["help_command"] = "bin/byteworker " + " ".join(parts) + " --help"
    elif len(parts) > 2:
        result["command_path"] = ".".join(parts)
        result["help_command"] = "bin/byteworker " + " ".join(parts) + " --help"
    return {"schema_version": DESCRIPTION_VERSION, "command": result}


def command_manifest(*, include_hidden: bool = False) -> dict[str, object]:
    specs = command_specs(include_hidden=include_hidden)
    return {
        "schema_version": MANIFEST_VERSION,
        "commands": [spec.to_dict() for spec in specs],
        "aliases": [alias.to_dict() for alias in command_aliases()],
        "namespaces": list(namespace_names()),
        "output_policy": output_policy(),
        "count": len(specs),
    }


def search_commands(query: str, *, include_hidden: bool = False) -> dict[str, object]:
    terms = [value.casefold() for value in query.split() if value]
    matches = []
    for spec in command_specs(include_hidden=include_hidden):
        haystack = " ".join(
            (spec.name, spec.summary, spec.category, spec.docs, *spec.operations)
        ).casefold()
        if all(term in haystack for term in terms):
            matches.append(spec.to_dict())
    alias_matches = []
    for alias in command_aliases():
        haystack = " ".join((*alias.path, *alias.target, alias.summary)).casefold()
        if all(term in haystack for term in terms):
            alias_matches.append(alias.to_dict())
    return {
        "schema_version": MANIFEST_VERSION,
        "query": query,
        "commands": matches,
        "aliases": alias_matches,
        "count": len(matches) + len(alias_matches),
        "command_count": len(matches),
        "alias_count": len(alias_matches),
    }


def render_top_level_help(*, include_hidden: bool = False) -> str:
    groups: dict[str, list[CommandSpec]] = {}
    for spec in command_specs(include_hidden=include_hidden):
        groups.setdefault(spec.category, []).append(spec)
    labels = {
        "system": "System",
        "source": "Sources",
        "digest": "Digest",
        "knowledge": "Knowledge base",
        "productivity": "Productivity",
        "automation": "Automation",
        "external": "External tools",
        "maintainer": "Maintainer",
        "compatibility": "Compatibility",
    }
    lines = [
        "usage: byteworker [--pretty] <command> [arguments...]",
        "",
        "Byteworker runtime-safe command launcher.",
        "",
        "Preferred namespaces:",
    ]
    namespace_summaries = {
        "system": "启动、依赖与运行时管理",
        "source": "来源授权、抓取与 Wiki 探索",
        "digest": "digest 生命周期、观测与底层工具",
        "kb": "知识库查询、维护与写入",
        "automation": "报告与 Dreaming 自动化",
        "external": "外部 CLI",
        "maintainer": "维护者工具",
    }
    for namespace in namespace_names():
        lines.append(f"  {namespace:<24} {namespace_summaries[namespace]}")
    lines.extend(("", "Compatible direct commands:", ""))
    for category, specs in groups.items():
        lines.append(labels.get(category, category.title()) + ":")
        for spec in specs:
            suffix = " [advanced]" if spec.visibility == "advanced" else ""
            if spec.stability != "stable":
                suffix += f" [{spec.stability}]"
            lines.append(f"  {spec.name:<24} {spec.summary}{suffix}")
        lines.append("")
    lines.extend(
        (
            "options:",
            "  -h, --help               显示帮助并退出",
            "  --all                    在顶层帮助/commands 中包含隐藏兼容入口",
            "  --pretty                 缩进 facade JSON 输出，必须放在 command 前",
            "",
            "运行 `bin/byteworker <command> --help` 查看参数。",
            "运行 `bin/byteworker commands list --json` 获取机器可读命令清单。",
        )
    )
    return "\n".join(lines) + "\n"


def render_command_help(spec: CommandSpec) -> str:
    usage = spec.usage or f"byteworker {spec.name} [arguments...]"
    lines = [f"usage: {usage}", "", spec.summary, ""]
    if spec.operations:
        lines.append("operations:")
        lines.append("  " + ", ".join(spec.operations))
        lines.append("")
    if spec.options:
        lines.append("options:")
        for option, summary in spec.options:
            lines.append(f"  {option:<24} {summary}")
        lines.append("")
    lines.extend(
        (
            f"audience: {spec.audience}",
            f"side-effect: {spec.side_effect}",
            f"documentation: {spec.docs}",
        )
    )
    return "\n".join(lines) + "\n"


def validate_registry() -> list[str]:
    errors: list[str] = []
    allowed_execution = {"launcher", "facade", "facade-special", "external"}
    for spec in COMMAND_SPECS:
        if spec.execution not in allowed_execution:
            errors.append(f"{spec.name}: invalid execution {spec.execution}")
        if spec.execution == "facade" and not spec.entrypoint:
            errors.append(f"{spec.name}: facade command has no entrypoint")
        if not spec.summary.strip():
            errors.append(f"{spec.name}: missing summary")
        if not spec.docs.strip():
            errors.append(f"{spec.name}: missing docs")
    for alias in COMMAND_ALIASES:
        spec = command_spec(alias.target[0])
        if spec is None:
            errors.append(
                f"{'.'.join(alias.path)}: unknown target {'.'.join(alias.target)}"
            )
            continue
        if len(alias.target) > 1 and alias.target[1] not in spec.operations:
            errors.append(
                f"{'.'.join(alias.path)}: unknown target operation "
                f"{'.'.join(alias.target)}"
            )
    return errors


def names(specs: Iterable[CommandSpec]) -> tuple[str, ...]:
    return tuple(spec.name for spec in specs)
