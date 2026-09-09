# `bin/` 命令架构审查

> 生命周期：`point_in_time`。本文保留审查基线和当时建议，不覆盖当前架构文档。
>
> 审查日期：2026-09-09
>
> 审查基线：当前工作树，`HEAD=bbaea34`
>
> 审查范围：`bin/`、统一启动链、机器协议、命令相关 reference、架构约束与测试
>
> 本文只做架构审查和改进建议，不改变现有命令行为。

## 1. 结论摘要

当前命令体系的基础方向是正确的：

- `bin/byteworker` 先解析兼容 Python，再进入 launcher，避免 Agent 猜测运行环境；
- `byteworker-cli/v1` 为大部分确定性工具提供统一 envelope；
- 复杂业务多数已经下沉到 `lib/`，provider 差异也有 registry / adapter 边界；
- Agent 按 workflow reference 获取命令，而不是常驻加载完整手册，符合 token 成本约束；
- 写入、临时 artifact、兼容入口和人工排障边界已有较完整文档。

但命令面已经超过“手工维护若干列表和一份大手册”能够可靠治理的规模。当前最主要的问题不是命令数量本身，而是缺少一个能同时驱动路由、帮助、运行时依赖、文档和测试的命令元数据真相源。其直接后果已经出现：

1. launcher 自有命令、facade 工具、手册列表、Agent reference 和测试列表彼此不一致；
2. `--help` 在不同层级有不同协议，部分帮助失败、依赖外部 runtime，甚至可能触发状态修改；
3. `bin/` 平铺混合公开命令、内部执行器、兼容 tombstone 和 shell 集成，单看目录无法判断应使用哪个入口；
4. `todo.py`、`dreaming.py`、`source.py` 等入口继续增长，参数、分发和部分应用逻辑集中在单文件；
5. 每次调用经过多层进程启动和 stdout 全量缓冲，暖缓存下本地轻命令约有 130 ms 的额外固定成本。

总体判断：**当前架构可用且安全边界总体良好，但发现机制和演进机制已进入高漂移区。应先治理命令模型与帮助契约，再考虑目录重排或性能优化。**

## 2. 审查方法与边界

本次审查执行了以下工作：

- 清点 `bin/` 文件、文件模式、行数和 facade 注册项；
- 阅读 [`ARCHITECTURE.md`](../../ARCHITECTURE.md)、[`bin/README.md`](../../../../bin/README.md)、[`machine-protocol.md`](../../../../references/machine-protocol.md) 和 workflow routes；
- 检查 shell bootstrap、Python launcher、facade、runtime cache 和主要大入口；
- 实际运行顶层帮助、工具帮助、嵌套帮助和 launcher 原生命令帮助；
- 对帮助路径和轻量本地命令做 8 次暖缓存计时；
- 检查命令文档契约、机器协议测试和 launcher 测试覆盖范围；
- 对照安装文档、运行时代码和架构文档中的依赖要求。

未执行的内容：

- 未对真实飞书、Meego、风神或真实知识库做端到端写入；
- 未以生产 workload 做 profiler，因此性能结论只覆盖本地固定开销；
- 未修改或恢复审查开始前已有的工作树变更。

## 3. 当前命令模型

### 3.1 规模

当前 `bin/` 共 39 个文件，其中 1 个是命令手册，另有 38 个入口、helper 或内部执行器。`bin/` 实现约 7,323 行，命令手册 1,226 行。

facade 的 [`TOOLS`](../../../../bin/byteworker-cli.py#L24-L46) 注册 21 个底层工具，另有虚拟命令 `update-status`，形成 22 个 facade 命令。launcher 还直接处理 6 类入口：

- `preflight`
- `deps`
- `runtime-reset`
- `lark`
- `meegle`
- `run`

目录中还存在 viewer、update、评论拉取、群聊拉取、索引/链接底层执行器等不经过 facade 的入口。

### 3.2 实际分层

```text
自然语言 / slash command
        |
        v
SKILL.md + workflow routes + references
        |
        v
bin/byteworker                         shell bootstrap
        |
        v
bin/byteworker-launcher.py             runtime 与原生命令分发
        |
        +--> preflight / deps / lark / meegle / run
        |
        v
bin/byteworker-cli.py                  facade 与 envelope
        |
        v
bin/<tool>.py                          argparse / I/O adapter
        |
        v
lib/<application or domain>.py         应用服务、领域规则、存储与 adapter
```

该分层在主路径上是合理的。问题集中在“每层有哪些命令、命令具有什么属性、如何展示和验证”没有统一模型。

### 3.3 使用者实际上面对三个产品面

| 产品面 | 主要使用者 | 示例 | 现状 |
|---|---|---|---|
| Agent 语义入口 | 最终用户、Agent | `/byteworker digest`、自然语言 Todo | 由 `SKILL.md` 和 workflow routes 管理，方向合理 |
| 确定性机器入口 | Agent、自动化 | `bin/byteworker source capture ...` | 有统一 envelope，但命令发现和元数据不足 |
| 直接/兼容/运维入口 | 维护者、旧调用方 | `pull-chat.sh`、`rebuild_index.py`、`inbox.py` | 与公开入口平铺，生命周期和风险只能查手册 |

三个产品面应继续存在，但必须在命令模型和帮助输出中显式区分，不能只依赖维护者记忆。

## 4. 做得好的部分

### 4.1 稳定启动链设计正确

[`bin/byteworker`](../../../../bin/byteworker) 在加载仓库 Python 模块前解析 Python，并确保 update-check 先于模块 import。launcher 再通过 [`runtime_deps.py`](../../../../lib/runtime_deps.py) 统一外部程序路径和环境。这解决了 Agent 环境中常见的 PATH、NVM、venv 和版本漂移问题。

Python/runtime cache 仍会验证路径有效性，优先保证正确性而不是盲目信任缓存。这个取舍合理，不建议为了几十毫秒直接删除验证。

### 4.2 机器协议边界清楚

facade 将成功、需关注和错误统一为 `byteworker-cli/v1`，保留下游退出码，并对 doctor 的 attention 状态做显式映射。artifact 大多通过受控文件传递，而不是把大段业务正文放入 argv 或日志，符合隐私与运行稳定性要求。

### 4.3 Agent 的按需路由优于集中大手册

[`workflow-routes.json`](../../../../references/workflow-routes.json) 将 Agent 语义任务路由到有限 reference，避免每次加载 1,226 行命令手册。对于已知 workflow，Agent 通常可以直接找到正确命令，这比让 Agent 自由扫描 `bin/` 更可靠。

### 4.4 兼容债务已有部分显式记录

索引和 links 的 shell/Python 双入口、旧 profile、旧 report owner 等兼容边界已在架构文档中给出删除条件。保留兼容 facade 而不是直接删除旧入口是正确做法。

## 5. 发现清单

本文使用以下优先级：

- `P1`：已造成错误发现、错误操作或持续架构漂移，应优先修复；
- `P2`：会显著增加扩展和维护成本，应进入近期治理；
- `P3`：优化项，需结合真实 workload 和迁移成本决定。

本次没有发现需要立即停止所有命令使用的 `P0` 问题。

### F-01 `P1` 命令注册信息存在多个真相源，并且已经漂移

**证据**

- facade 实际暴露 22 个命令；
- [`bin/README.md`](../../../../bin/README.md#L25-L45) 的“支持的 tool”只列 19 个，遗漏 `digest-flow`、`workflow-budget`、`inbox`；
- [`machine-protocol.md`](../../../../references/machine-protocol.md#L32-L34) 列 20 个，遗漏 `inbox`、`semantic`；
- [`test_registered_tool_help_passes_through_launcher`](../../../../tests/test_machine_protocol.py#L129-L164) 手写 19 个工具，遗漏 `inbox`、`semantic`；`update-status` 由另一测试单独覆盖；
- launcher 的 6 类原生命令不在 facade `TOOLS` 中，也不在顶层帮助中。

**影响**

- 人和 Agent 看到不同文档会得到不同命令集合；
- 新增命令需要同步代码、多个文档列表和测试列表，遗漏是默认结果而不是异常；
- 命令是否公开、是否兼容、是否内部、是否有副作用无法机器判断；
- runtime 要求、attention 退出码和 operation 提取继续以额外条件分支维护。

**建议**

建立唯一 `CommandSpec` registry，由它生成或校验：顶层帮助、`commands list/describe`、facade 分发、launcher runtime 需求、文档索引和参数化测试。手册不再手写“支持命令”列表。

### F-02 `P1` 顶层帮助不是完整的 launcher 帮助

**证据**

`bin/byteworker --help` 直接落到 facade parser，只展示 22 个 facade 命令，不展示 `preflight`、`deps`、`runtime-reset`、`lark`、`meegle`、`run`。但这些入口是稳定启动链的重要组成部分，多个 reference 又要求 Agent 使用它们。

顶层帮助还只显示名字，不显示任何工具摘要。用户看到的是一行长 choice 列表，无法判断 `digest-flow`、`digest-run`、`digest-job`、`digest-txn` 的区别。

**影响**

- `--help` 不能回答“有哪些命令”；
- 人工排障必须先知道命令名，才能继续猜或搜索文档；
- facade 工具和 launcher 原生命令形成隐式双层 namespace。

**建议**

由 launcher 持有统一顶层 parser，按类别输出所有稳定入口及一句话摘要。facade 作为执行协议，不再拥有独立且不完整的顶层命令目录。

### F-03 `P1` `--help` 不满足“任意层级、只读、零依赖、退出 0”契约

**已复现**

- `bin/byteworker source --help` 输出普通 argparse 文本；
- `bin/byteworker source bundle --help` 输出 `byteworker-cli/v1` envelope，帮助文本嵌在 `data` 字符串；
- `bin/byteworker dreaming report --help` 同样被 envelope 包装；
- `bin/byteworker deps --help` 退出 2，并报告只接受 `--refresh / --cache-status`；
- `bin/byteworker run --help` 把 `--help` 当可执行文件，退出 1；
- `bin/byteworker inbox --help` 返回 `INBOX_REMOVED`，退出 2。

**静态确认**

- [`_tool_help_request`](../../../../bin/byteworker-cli.py#L133-L140) 只识别恰好为 `<tool> --help` 的顶层帮助；
- [`byteworker-launcher.py`](../../../../bin/byteworker-launcher.py#L152-L168) 对 `runtime-reset` 忽略所有剩余参数，因此 `runtime-reset --help` 会执行 cache clear，而不是展示帮助；
- launcher 在转发前先计算 `required_sources`。例如缺少相应 provider runtime 时，`wiki inspect --help` 或带 source type 的来源帮助可能在 argparse 展示前失败。

**影响**

- 帮助路径可能修改状态，违反架构文档中的只读发现原则；
- 缺依赖时恰恰最需要帮助，但当前可能无法看到帮助；
- Agent 需要根据层级决定是否解析 JSON，增加分支和误判；
- shell 用户不能依赖通用的 `<path> --help` 心智模型。

**建议**

将帮助检测放在任何 runtime 探测和状态操作之前，并支持任意深度。所有公开命令满足：

```text
<command path> -h|--help
=> exit 0
=> stdout 为普通帮助文本
=> stderr 为空
=> 不要求 KB、provider 登录或外部 CLI
=> 不产生、删除或修改任何状态文件
```

Agent 所需结构化发现使用单独的 `commands describe ... --json`，不要把人类帮助文本塞进业务 envelope。

### F-04 `P1` `bin/` 的公开、内部、兼容和运维边界只存在于文档中

**证据**

同一平铺目录同时包含：

- 稳定入口：`byteworker`；
- facade 工具：`source.py`、`kb-query.py` 等；
- launcher/facade 内部实现：`byteworker-launcher.py`、`byteworker-cli.py`；
- 内部执行器：`viewer-server.py`、`update-state.py`、`rebuild_index.py`；
- 人工运维：`browse.sh`、`pull-chat.sh`、`resolve-users.sh`；
- 兼容入口：`rebuild-index.sh`、`repair-links.sh`；
- tombstone：`inbox.py`。

`inbox` 仍作为普通 facade choice 出现在顶层帮助，但在 Agent 公共协议中被遗漏；这说明 lifecycle 状态没有真正进入命令模型。

**影响**

- 新维护者和 Agent 容易直接调用内部执行器；
- 同一能力可能从 facade、shell wrapper 或 Python core 三条路径进入，输出和事务边界不同；
- “命令很多”主要是认知负担，而不是文件数量本身。

**建议**

先做逻辑分层，不急于物理移动文件：为每个命令声明 `audience`、`stability`、`visibility`、`side_effect`、`preferred_entry`。顶层默认只展示 stable public commands；`--all` 或 `commands list --audience maintainer` 再展示 advanced/internal/compat。

物理目录调整放在兼容别名稳定后进行，避免一次性破坏脚本路径。

### F-05 `P2` digest 能力以 7 个同级工具暴露，标准路径不突出

当前同级存在：

- `digest-flow`
- `digest-txn`
- `digest-run`
- `digest-analysis`
- `digest-capture`
- `digest-parallel`
- `digest-job`

其中 `digest-flow` 是标准生命周期编排，其他多数是观测、底层事务、并发 worker 或 Wiki batch 能力。但顶层帮助将它们等权展示，也没有摘要。

**影响**

- Agent 若未先加载完整 workflow reference，难以判断从哪个入口开始；
- 新增 digest 阶段自然倾向于再增加一个顶层 `digest-*` 文件；
- 编排细节泄漏到调用方，增加漏记 lifecycle event 或错误组合的概率。

**建议**

在兼容现有名字的前提下，定义逻辑 namespace：

```text
digest flow ...                 标准入口
digest job ...                  多页批次
digest inspect run|analysis ... 高级诊断
digest internal txn|capture|parallel ... 仅 Agent workflow / 维护者
```

短期不必改命令名，只需在 registry 中标明 preferred path 和层级，并让帮助首先推荐 `digest-flow`。

### F-06 `P2` 大型入口文件正在侵蚀“薄 CLI”边界

**证据**

- [`todo.py`](../../../../bin/todo.py) 755 行，包含时间自然语言解析、Markdown store 解析/渲染、事务和命令分发，而不仅是 argparse/I/O；
- [`dreaming.py`](../../../../bin/dreaming.py) 802 行，定义 20 个顶层 operation 和多组嵌套 operation，parser 与 handler 形成大型手写路由；
- [`source.py`](../../../../bin/source.py) 511 行，`inspect/capture` 共享一组 provider 参数，main 还负责 artifact 写入、Bundle 转换和 receipt shaping；
- 架构文档要求 `bin/source.py` 保持薄，但新增 provider 当前仍可能要求扩展这一共享参数面。

**影响**

- parser、应用编排和领域规则难以独立测试和复用；
- 新增 operation 需要修改集中式 `if/elif` 分发；
- 大文件的所有功能共享 import 成本和变更冲突；
- facade 无法从底层自动获得 operation 元数据。

**建议**

- 将 Todo 领域与存储逻辑移至 `lib/todo.py`，保留兼容 wrapper；
- Dreaming 各子域提供 `register_commands(subparsers)` 和独立 handler，顶层只组合命令；
- Source operation adapter 同时声明参数 schema、runtime requirements 和 handler，避免 `source.py` 继续堆 provider flag；
- entrypoint 只负责 parse、调用 application service、输出标准结果和映射已声明异常。

不要仅按行数机械拆文件。拆分标准应是职责、依赖方向和独立测试边界。

### F-07 `P2` 参数语法、KB 定位和可执行文件模式不一致

**证据**

- 多数工具采用 `<operation> --kb <path>`；
- Todo 采用 `todo <kb_dir> <operation>`；
- Doctor 采用可选位置参数 `[scan|fix]`，而非 subparser；
- 一些工具默认读取 `.kbconfig`，另一些强制 `--kb`，另一些要求位置参数；
- facade 注册的 `kb-mutate.py`、`context.py`、`semantic.py` 文件模式为 `0644`，其他注册脚本多为 `0755`；
- 命令描述和错误信息混合中英文。

**影响**

- 人和 Agent 无法迁移已有命令经验，只能逐个查文档；
- facade 的 [`_operation`](../../../../bin/byteworker-cli.py#L72-L78) 需要基于位置参数做启发式判断；
- 直接排障入口是否可直接执行由文件模式偶然决定。

**建议**

新命令统一采用：

```text
bin/byteworker [global options] <namespace> <operation> [operation options]
--kb <path> > BYTEWORKER_KB > .kbconfig
```

旧 Todo/Doctor 语法保留兼容别名，并在 machine response 中返回 deprecation 元数据。对直接 Python 排障入口，要么统一可执行位，要么明确只支持 `python3 bin/<tool>.py`，不要混用。

### F-08 `P2` 帮助内容覆盖不足

实际顶层帮助情况：

- facade 的 22 个 choice 全部没有一句话摘要；
- Dreaming 20 个顶层 operation 没有摘要；
- Todo 9 个 subcommand 没有摘要；
- Wiki 6 个、digest-job 7 个、report-automation 8 个 subcommand 没有摘要；
- Source 的 `inspect`、`capture` 缺少摘要，且若干参数没有 help；
- 多数大型命令的参数只有名字，没有用途、输入格式、默认值边界或副作用说明。

目前 `bin/README.md` 补足了大量信息，但这意味着用户必须在运行帮助和 1,226 行手册之间切换。

**建议**

公开命令的 parser 至少提供：summary、side-effect 提示、参数语义、默认值、示例、退出码链接。复杂 JSON 输入继续通过 `*-spec` 或 `commands describe --json` 暴露，不要把完整 schema 塞进短帮助。

### F-09 `P2` 文档契约只检查“文件名出现”，不能阻止语义漂移

[`test_every_bin_command_is_documented`](../../../../tests/test_bin_readme_contract.py#L14-L23) 只检查 README 是否包含反引号包裹的文件名。它不能确认：

- 命令是否在正确类别；
- 是否标注 stable/internal/compat；
- 参数、输出协议和副作用是否准确；
- facade 注册项与手册“支持 tool”列表是否一致；
- `--help` 是否真正成功且只读。

机器协议帮助测试又维护了一份手写工具列表，因此无法发现遗漏项。

**建议**

所有契约测试从 registry 参数化，至少覆盖：

1. 每个 visible command 出现在顶层帮助和结构化 manifest；
2. 每个 public command 的每一级 `--help` 退出 0；
3. help 前后仓库和 cache/state 快照一致；
4. 缺少 optional provider runtime 时帮助仍可用；
5. 文档索引由 registry 生成或与 registry 严格比较；
6. deprecated/tombstone 不出现在默认 stable 列表；
7. command path、文件模式、handler、protocol 和 docs link 均有效。

### F-10 `P2` 安装文档与真实 Python 门禁不一致

[`README.md`](../../../../README.md#L83) 和 [`INSTALL.md`](../../../../INSTALL.md#L124) 写 `python3 >= 3.9`，但 [`bin/byteworker`](../../../../bin/byteworker#L19-L23)、DESIGN、machine protocol 和 session preflight 要求 Python >= 3.10 且有 `zoneinfo`。

**影响**

用户可能按安装文档得到“依赖满足”的判断，随后稳定入口立即拒绝运行。这是命令发现和上手链路中的实际断点。

**建议**

以 runtime resolver 中的版本要求为唯一常量或生成来源，安装文档和依赖报告引用同一值；增加安装文档契约测试。

### F-11 `P3` 多层进程和全量 stdout 缓冲带来固定开销

本机暖缓存、每项 8 次的结果：

| 路径 | 中位耗时 |
|---|---:|
| `python3 bin/source.py --help` | 70.1 ms |
| `python3 bin/byteworker-cli.py source --help` | 132.5 ms |
| `bin/byteworker --help` | 102.1 ms |
| `bin/byteworker source --help` | 201.4 ms |
| `bin/byteworker source capabilities` | 201.4 ms |

完整路径通常包含 shell 的 Python 校验、launcher Python、facade Python和底层 tool Python。facade 又在 [`_run_tool`](../../../../bin/byteworker-cli.py#L151-L210) 中完整捕获 stdout/stderr、解析 JSON并重新序列化。

**判断**

对网络抓取、Git 事务和模型调用，这部分不是主耗时；对一条 workflow 内的几十次轻量状态命令，固定成本会累积。stdout 全量缓冲还要求底层始终遵守有界输出，否则存在峰值内存和延迟风险。

**建议**

- 先增加真实 workflow 的 command count、p50/p95 和输出大小观测；
- 优先用 `digest-flow` 等应用服务合并高频细粒度调用，而不是直接取消进程隔离；
- 对大结果继续返回受控 artifact path，只让 envelope 携带摘要；
- 若固定成本成为瓶颈，再评估 launcher 直接执行 registry handler 的单进程 fast path；
- fast path 必须保留 update-before-import、runtime 环境、异常映射和测试隔离，不能用复制逻辑换性能。

### F-12 `P3` facade 的协议属性继续依赖特例代码

当前存在以下手写特例：

- `ATTENTION_EXIT_CODES = {"doctor": {2}}`；
- `_operation()` 对 Todo 做位置参数特判；
- launcher `_required_sources()` 手工解析 Wiki/Source 参数；
- Doctor 自动追加 `--format json`；
- `update-status` 不映射到底层文件，而是 facade 内部实现。

这些特例单独看都合理，但它们证明命令的 runtime、operation、输出和状态语义实际上是领域元数据，不应散落在 dispatcher 分支中。

**建议**

将这些属性移入 `CommandSpec`，并允许每个 command adapter 提供明确的 `operation_resolver` 和 `result_policy`。禁止在通用 facade 中继续按名字增加条件分支。

### F-13 `P3` `run` 是高权限逃生口，但没有在命令模型中标记

`bin/byteworker run <command>` 可以在解析后的 runtime 环境中执行任意命令。对当前 coding agent 而言这未必扩大宿主权限，但它绕过 facade envelope、side-effect 分类和输出边界。

**建议**

保留该能力用于受控 helper，但将其标记为 `advanced/internal`，默认帮助只给用途和风险，不把它描述成普通业务命令。Agent reference 应优先给出 allowlisted helper 的完整路径，避免自然语言直接拼任意命令。

## 6. 目标架构

### 6.1 单一命令注册表

建议新增 stdlib-only 的声明式 registry，例如 `lib/command_registry.py`：

```python
CommandSpec(
    path=("source", "capture"),
    summary="抓取一个已支持来源",
    entrypoint="bin/source.py",
    audience="agent",
    stability="stable",
    visibility="public",
    side_effect="artifact-write",
    output_protocol="byteworker-cli/v1",
    required_runtimes=("python",),
    conditional_runtimes="source_type",
    docs="references/digest-core.md",
    preferred=True,
)
```

registry 至少包含：

| 字段 | 用途 |
|---|---|
| `path / aliases` | 唯一命令路径和兼容别名 |
| `summary` | 顶层与子层帮助摘要 |
| `audience` | user、agent、automation、maintainer、internal |
| `stability` | stable、advanced、deprecated、tombstone、internal |
| `side_effect` | none、temp-write、state-write、kb-write、external-write、arbitrary-exec |
| `entrypoint / handler` | 执行目标，不再维护平行 TOOLS dict |
| `runtime requirements` | core 和 conditional provider runtime |
| `output protocol` | envelope、plain、stream、external passthrough |
| `result policy` | attention exit code、错误映射、operation resolver |
| `docs / examples` | 人和 Agent 的按需说明入口 |

### 6.2 双通道发现

为人提供普通帮助：

```bash
bin/byteworker --help
bin/byteworker digest --help
bin/byteworker source capture --help
```

为 Agent 和自动化提供稳定 manifest：

```bash
bin/byteworker commands list --json
bin/byteworker commands describe source.capture --json
bin/byteworker commands search "读取飞书文档" --json
```

`describe` 应返回参数 schema、side-effect、runtime、协议、文档路径和最小示例。这样 Agent 不需要读完整手册，也不需要解析人类帮助文本。

### 6.3 逻辑 namespace

推荐的目标信息架构：

```text
system       preflight, deps, runtime, update-status
source       auth, inspect, capture, bundle, profile, diff
digest       flow, job, run-inspect, internal stages
kb           query, mutate, index, doctor, provenance
todo         init, add, list, check, status, snooze, edit
automation   report, dreaming
external     lark, meegle
maintainer   browse, update, repair, helper-run
```

这不是要求立即重命名全部命令。第一阶段应把现有命令映射到这些类别并标记 preferred path；只有在结构稳定、别名和遥测证明安全后，才逐步引入新的分组路径。

### 6.4 薄入口与模块化 parser

目标依赖方向：

```text
command spec -> parser adapter -> application service -> domain/ports
                                     |
                                     v
                              provider adapter / store
```

约束：

- `bin/` 不定义领域 model、持久化格式或事务算法；
- parser adapter 可做类型转换和必填校验，不做 provider 业务分支；
- application service 返回 typed result，不直接决定 facade envelope；
- provider adapter 声明自己的 CLI schema 和 runtime requirements；
- compatibility wrapper 只翻译旧 argv 到新 command request，不复制实现。

## 7. 改进路线

### Phase 0：修复发现与安全契约

目标：不改业务命令名，不做大规模文件移动。

1. 修复任意深度 `--help`，确保只读、零 optional dependency、退出 0；
2. 修复 `deps`、`run`、`runtime-reset`、`inbox` 的帮助行为；
3. 顶层帮助纳入 launcher 原生命令，并为每个命令增加摘要；
4. 从实际注册表参数化所有帮助测试；
5. 修正文档中的 Python 3.9/3.10 不一致；
6. 给 tombstone/deprecated 命令增加明确 visibility，不在默认 stable 列表展示。

### Phase 1：引入单一 registry 和结构化发现

1. 引入 `CommandSpec`，先描述现有路径，不改变执行实现；
2. 用 registry 替代 `TOOLS`、`ATTENTION_EXIT_CODES`、`_required_sources` 和手写测试列表；
3. 增加 `commands list/describe --json`；
4. 由 registry 生成 `bin/README.md` 的命令总览，详细 narrative 仍人工维护；
5. 在 envelope context 中加入稳定 `command_path`、`stability` 和 `side_effect`，不回显敏感 argv。

### Phase 2：收敛大型入口

1. 把 Todo 领域/存储逻辑下沉到 `lib/`；
2. 将 Dreaming parser/handler 按 `schedule`、`process`、`report`、`action`、`review` 拆分注册；
3. 让 Source adapter 声明 inspect/capture 参数和 runtime 需求；
4. 为 compatibility wrapper 建立删除条件和真实调用观测；
5. 明确所有 direct/internal 文件的执行位与支持方式。

### Phase 3：信息架构与性能优化

1. 引入新的分组命令别名，旧路径进入有期限的 deprecation；
2. 根据真实 workflow 数据决定是否实现单进程 fast path；
3. 为高频流程增加批量 application command，减少往返次数；
4. 达到兼容删除条件后，再考虑把内部执行器移出平铺 `bin/`。

## 8. 验收标准

### 8.1 发现性

- `bin/byteworker --help` 完整展示所有 stable public 命令及摘要；
- 100% public command path 的 `--help` 在无 KB、无 provider CLI、未登录条件下可读；
- 任意 help 调用前后 cache、state、KB 和 Git 状态完全不变；
- `commands list --json` 与实际 dispatcher 集合完全一致；
- `commands describe` 能回答参数、协议、副作用、runtime、文档和 preferred path。

### 8.2 可扩展性

- 新增命令只新增一份 CommandSpec 和对应 handler/test，不手工同步多个列表；
- 新增 provider 不修改通用 facade 和 launcher 名字分支；
- 通用 dispatcher 中不再出现新的 `if tool == ...` 特例；
- public entrypoint 不包含领域持久化和事务实现。

### 8.3 易用性与兼容

- 所有新命令统一 `--kb` 解析优先级；
- deprecated alias 返回明确迁移目标，不静默改变语义；
- 默认帮助隐藏 internal/tombstone，但 `--all` 可审计；
- 人类帮助为 plain text，机器发现为 JSON，不混合两种协议。

### 8.4 性能与稳定性

- 为 `--help`、`commands list`、轻量只读命令建立 CI 基准或回归阈值；
- facade success 输出有明确大小上限，大结果只返回 artifact receipt；
- 优化前后 update-before-import、runtime cache、错误映射和隔离测试全部通过；
- 用完整 workflow 的总耗时和命令次数评估收益，不只优化单个微基准。

## 9. 建议新增的契约测试

```text
test_top_level_help_contains_launcher_and_facade_commands
test_every_registry_command_has_summary_and_docs
test_every_public_help_path_is_read_only
test_nested_help_is_plain_text_and_exit_zero
test_help_does_not_require_optional_runtime
test_runtime_reset_help_does_not_clear_cache
test_deps_and_run_help_are_valid
test_tombstones_are_hidden_from_default_help
test_generated_command_index_matches_registry
test_documented_python_version_matches_runtime_requirement
test_public_direct_entrypoint_mode_is_consistent
test_large_result_returns_artifact_receipt
```

其中“只读”测试不能只 mock handler，应在临时目录记录 help 前后的文件清单、内容 hash 和 mtime，并执行真实 launcher 路径。

## 10. 最终建议

优先顺序应是：

1. **先修 help 安全和完整性**，因为这是人和 Agent 的共同入口，且已有可复现错误行为；
2. **再建立单一 CommandSpec registry**，从机制上消除列表、runtime、协议和测试漂移；
3. **随后收敛大型入口和 digest 命令层级**，降低继续扩展时的集中式修改；
4. **最后基于真实观测优化进程开销**，不要为了微基准破坏现有的隔离和 update-before-import 保证。

不建议当前直接做一次“大重命名 + 大搬目录”。那会同时触发 Agent reference、自动化、旧调用方和兼容脚本迁移，风险高且无法先解决命令元数据缺失。先用 registry 把当前事实建模，再让帮助、文档和测试由事实生成，才是可控的演进路径。

## 11. 本次验证记录

审查开始时工作树已有以下未提交文件，本文没有修改它们：

```text
SKILL.md
references/dreaming-onboarding.md
references/dreaming-setup-guide.md
references/workflow-routes.json
tests/test_agent_route_contract.py
tests/test_architecture_contract.py
```

新增文件只有本文。验证结果：

| 检查 | 结果 |
|---|---|
| 命令、launcher、手册与架构相关测试 | 43 tests passed |
| 完整 `unittest discover` | 635 tests passed，47.619 s |
| `python3 -m compileall -q bin lib tests` | passed |
| 全部 `bin/*.sh` 的 `bash -n` | passed |
| `git diff --check` | passed |
| 文档相对链接目标检查 | passed |
| coverage.py branch coverage 门禁 | 未执行：当前 Python 环境没有安装 `coverage` |
| `tests/run` | 未执行：仓库中不存在该入口 |

coverage 环境缺失不影响本文的静态发现和实际帮助复现，但意味着本次验证不能替代正式实现改动时的覆盖率门禁。
