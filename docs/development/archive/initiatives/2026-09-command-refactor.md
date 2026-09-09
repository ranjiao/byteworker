# `bin/` 命令架构重构任务

> 生命周期：`completed`。本文保留重构任务和验收记录，不再作为活跃计划。
>
> 来源：[`2026-09-command-architecture.md`](../../evidence/reviews/2026-09-command-architecture.md)
>
> 原则：先建立命令事实和兼容边界，再拆大型入口；不做一次性重命名或目录搬迁。

## 状态说明

- `complete`：代码、文档和仓库要求的全量门禁均已完成；
- `planned`：边界和验收标准明确，等待前置任务完成；
- `evidence-gated`：不是待实施代码；必须先取得真实调用或性能数据，才决定是否立项。

## 任务总览

| ID | 优先级 | 状态 | 任务 | 依赖 |
|---|---|---|---|---|
| CR-01 | P1 | complete | 建立统一 `CommandSpec` registry | 无 |
| CR-02 | P1 | complete | 修复完整且只读的帮助链路 | CR-01 |
| CR-03 | P1 | complete | 增加结构化命令发现 | CR-01 |
| CR-04 | P1 | complete | registry 驱动 facade/runtime/protocol 元数据 | CR-01 |
| CR-05 | P1 | complete | 文档去重与契约测试 | CR-01 至 CR-04 |
| CR-06 | P2 | complete | Todo 入口与领域/存储逻辑分层 | CR-05 |
| CR-07 | P2 | complete | Dreaming parser/handler 模块化 | CR-05 |
| CR-08 | P2 | complete | Source operation 参数 schema 下沉 adapter | CR-05 |
| CR-09 | P2 | complete | 引入逻辑 namespace 和兼容别名 | CR-06 至 CR-08 |
| CR-10 | P3 | complete | 优化轻命令固定开销和大输出边界 | CR-05 |

## CR-01 统一命令 registry

**目标**

命令名、入口、operation、使用者、可见性、生命周期、副作用、runtime、输出协议和文档只有一份事实。

**交付**

- `lib/command_registry.py`；
- facade entrypoints 和 attention exit codes 从 registry 生成；
- runtime requirement 由 registry 数据驱动；
- tombstone 使用 `visibility=hidden`、`stability=tombstone`。

**验收**

- registry 无重复命令；
- 每个 entrypoint 和 docs 路径存在；
- 通用 facade/launcher 不再为新命令维护第二份名单。

## CR-02 只读帮助链路

**目标**

任意公开命令层级的 `-h/--help` 在 runtime 探测、update、cache/state 修改和业务 handler 前返回。

**交付**

- 顶层帮助合并 launcher 与 facade 命令并按领域分组；
- 嵌套 argparse help 直接输出 plain text，不进入 envelope；
- `deps`、`run`、`runtime-reset`、`inbox`、`lark`、`meegle` 有稳定 wrapper help；
- shell bootstrap 在 help/commands discovery 时不写 Python cache；
- tombstone 默认不出现在顶层 stable 清单，`--all` 可审计。

**验收**

- help 退出 0、stderr 为空；
- 缺 optional provider runtime 时仍可读；
- `runtime-reset --help` 不调用 cache clear；
- help 前后 cache、state、KB 和 Git 状态不变。

## CR-03 结构化命令发现

**目标**

Agent 不解析人类帮助文本，也不加载完整 `bin/README.md`。

**交付**

```bash
bin/byteworker commands list --json
bin/byteworker commands describe source.capture --json
bin/byteworker commands search "查询证据" --json
```

协议：

- `byteworker-command-manifest/v1`
- `byteworker-command-description/v1`

**验收**

- 默认清单与实际可见 dispatcher 一致；
- `--all` 可返回 deprecated/tombstone；
- describe 返回 command path、帮助命令、runtime、side effect、protocol 和 docs。

## CR-04 registry 驱动协议与 runtime

**目标**

移除 dispatcher 中按命令名增长的协议特例。

**交付**

- `TOOLS`、attention exit codes、operation resolution 从 registry 派生；
- launcher 的 provider runtime 要求从 registry 派生；
- `byteworker-cli/v1.context` 增加 `command_path/stability/side_effect`；
- 保留已有 `tool/operation` 字段和协议版本，作为兼容的 additive change。

**验收**

- 旧命令 argv、退出码和 data/error 语义不变；
- Source/Wiki runtime fail-closed 行为不变；
- 新增工具不修改通用 dispatcher 条件分支。

## CR-05 文档和契约测试

**目标**

文档只解释行为和边界，不维护会漂移的命令名单。

**交付**

- `bin/README.md`、machine protocol、ARCHITECTURE、DESIGN 同步 registry；
- README/INSTALL 的 Python 要求统一为 >=3.10；
- 测试从 registry 参数化全部 facade help；
- 增加 registry、manifest、嵌套帮助、无 runtime 帮助、cache 不变和 tombstone visibility 测试。

**验收**

- compile、shell syntax、相关测试、全量测试、coverage gate 和 diff check 通过；
- 若本机缺 coverage，必须在具备依赖的隔离环境补跑，不把缺失误报为通过。

## CR-06 Todo 分层

**目标**

`bin/todo.py` 只保留 parser、调用和输出；时间解析、Markdown store、事务应用服务进入 `lib/`。

**实施边界**

- 先建立 `lib/todo_models.py`、`lib/todo_time.py`、`lib/todo_store.py`、`lib/todo_service.py`；
- 保留 `bin/todo.py` 旧 argv；
- 现有测试若直接 import `bin/todo.py` 内部符号，先迁移到公开 lib API，再缩 wrapper；
- 不改变 Todo Markdown schema、ID、journal 或 Git transaction。

**验收**

- 所有 Todo 行为 fixture 字节级一致；
- commit failure rollback、并发锁和时间边界测试通过；
- wrapper 不再定义领域 model 或持久化算法。

**结果**：`bin/todo.py` 从 755 行降至 124 行；model、time、store、service 四层已独立，
原 argv、Markdown schema 和事务回滚行为通过测试。

## CR-07 Dreaming CLI 模块化

**目标**

把大型 parser/handler 按子域拆分，保留单一 `dreaming` 命令和现有 argv。

**建议模块**

- `lib/dreaming_cli_schedule.py`
- `lib/dreaming_cli_process.py`
- `lib/dreaming_cli_report.py`
- `lib/dreaming_cli_action.py`
- `lib/dreaming_cli_review.py`

每个模块暴露 `register_commands(subparsers)` 和对应 handler。`bin/dreaming.py` 只组合 parser、验证 KB、调用 handler 和编码统一错误。

**验收**

- 所有现有 Dreaming 命令 path 和 JSON 回执不变；
- 每个顶层和嵌套 operation 有 summary；
- 子域测试可以不 import 其他 Dreaming CLI 子域；
- 不修改 scheduler/state/report 领域 schema。

**结果**：`bin/dreaming.py` 从 802 行降至 70 行；每个 leaf parser 直接绑定所属子域 handler，
所有顶层和嵌套命令均有摘要。

## CR-08 Source 参数 schema 下沉

**目标**

新增 provider 不再向 `bin/source.py` 的共享 `inspect/capture` parser 增加 flag 和分支。

**实施边界**

- operation adapter 声明 source type、参数 schema、runtime 和 handler；
- 通用 parser 从声明生成参数或接受受控 request file；
- `source capabilities` 同一份声明生成；
- Profile、Bundle 和 snapshot store 契约保持不变。

**验收**

- 新增测试 provider 不修改 `bin/source.py`、facade 或 launcher；
- 现有 provider 参数和错误码兼容；
- credential/path safety、完整分页和 artifact 权限测试通过。

**结果**：`bin/source.py` 从 511 行降至 183 行；operation adapter 声明参数、runtime 和
handler，`source capabilities`、argparse 与 launcher 复用同一声明，通用应用逻辑进入
`lib/source_cli_service.py`。

## CR-09 逻辑 namespace 与兼容别名

**前置证据**

需要至少一个发布周期的 command manifest 使用统计或已知调用方清单，确认哪些旧路径仍在使用。

**目标路径**

```text
system ...
source ...
digest flow|job|inspect|internal ...
kb query|mutate|index|doctor|provenance ...
todo ...
automation report|dreaming ...
external lark|meegle ...
maintainer ...
```

旧路径只能作为 registry alias 翻译到新 path，不复制 handler。未满足兼容删除条件前不得物理移动内部执行器。

**结果**：仓库内现有 Skill、reference、template 和测试仍广泛调用扁平路径，因此本阶段只增加
registry alias，不删除、不隐藏、不物理移动旧入口。顶层帮助优先展示 `system/source/digest/kb/
automation/external/maintainer` namespace；manifest 同时返回 `aliases`、`namespaces` 和每条命令的
`preferred_path`。launcher 在 runtime 探测前把新路径翻译到唯一旧 handler，namespace help 同样
走只读旁路。旧 argv、envelope 中的执行 tool 和退出码保持不变。

## CR-10 性能和输出边界

**前置证据**

- 真实 workflow 的命令次数、p50/p95 和累计固定开销；
- facade stdout 大小分布和最大值；
- 网络、Git、模型耗时与本地进程耗时占比。

**可能方案**

1. 先用 application service 合并高频细粒度调用；
2. 大结果统一返回 `0600` artifact receipt；
3. 固定成本仍显著时，再评估 registry handler 单进程 fast path；
4. fast path 必须保留 update-before-import、runtime 和异常映射边界。

没有上述证据，不以微基准为由取消进程隔离。

**结果**：真实 timing metadata 中已终止 run 的 active duration p50 为 136860 ms、p95 为
296851 ms，event 数 p50/p95 为 4/6；完整 launcher 相对直接工具的固定成本约 130 ms，因此保留
进程隔离，不引入 fast path。facade 改为 1 MiB 有界流式 stdout 和 64 KiB stderr 收集，越界 stdout
返回 `byteworker-cli-artifact/v1` 的 `0600` 临时 artifact receipt。正常小输出 p50 增量仅约
2 至 7 ms。完整方法和限制见 [`2026-09-command-performance.md`](../../evidence/benchmarks/2026-09-command-performance.md)。

## 最终验证收据

- 全量测试：656 项通过；
- 分支覆盖率：77.3%，通过 `.coveragerc` 的 75.0% 门禁；
- Python 编译检查：通过；
- `bin/*.sh` shell 语法检查：通过；
- `git diff --check`：通过；
- 工作流上下文预算：通过，未提高既有预算。
