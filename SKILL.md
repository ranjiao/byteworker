---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、妙记、会议、群聊、Meego 保存视图、飞书多维表格视图、风神看板、外部 blog/论文/wiki、本地 md 消化成结构化实体图，并保存和持续更新用户自己的自然语言思考，支持查询、更新、会前简报、看板、自然语言 Todo、自动日报/周报、全局工作上下文、可选主动后台机制 Dreaming 和兼容诊断。当用户要保存或查询工作资料、沉淀自己的思考、管理待办提醒、生成工作报告、配置自动信息分析或摘要提醒、启用/关闭/查看 Dreaming、通过 Dreaming 分析飞书 IM、检查知识库，或使用 /byteworker digest/search/update/brief/dashboard/todo/context/thinking/dreaming/doctor/help 时触发。
---

# byteworker

## 能力

| 意图 | 作用 |
|---|---|
| digest | 摄取文档、会议、群聊、结构化视图、网页或本地 Markdown |
| search | 查询知识库 |
| update | 更新已有知识 |
| brief | 生成会前简报 |
| dashboard | 查看或维护工作看板 |
| todo | 用自然语言管理待办和提醒 |
| context | 查看或维护全局工作上下文 |
| thinking | 保存或持续更新用户自己的自然语言认知与推演 |
| dreaming | 用自然语言设置、启停或查看后台信息助手；默认关闭 |
| doctor | 检查或修复 schema/skill 兼容问题 |
| help | 原样展示 `references/help.md` |

自然语言未写子命令时按意图匹配。没有 `daily` / `weekly` 用户子命令；自动报告由宿主任务或
自然语言补跑触发。

## 每个 Session 先做

TraeWork 桌面版中，运行 preflight 或访问 KB 前，先确认 `.kbconfig` 指向的 KB 绝对路径已作为
工作目录加入当前 TraeWork 项目；只加入 byteworker skill 仓库不够，`.kbconfig` 和 `--kb`
也不会授予 TraeWork Sandbox 目录权限。未加入时先提示用户添加，禁止用 `sudo`、`chmod`、复制
KB 或反复重试绕过。出现 `Operation not permitted` / `Permission denied` 且 skill 仓库可访问时，
优先检查此项；Dreaming 场景的完整提示见 `references/dreaming-harness-trae.md`。

首次使用本 skill 时只运行一次：

```bash
bin/byteworker preflight
```

访问尚未登记的飞书/Meego 来源时分别加 `--require feishu` / `--require meego`。

- 无输出且退出码 0：静默继续，**不要再分别运行** update-check、依赖检查、Todo check 或报告状态。
- 输出 JSON：只处理 `byteworker-session-preflight/v1.notices`；blocking 先解决。
- launcher 在加载 Python 模块前完成更新，因此本次 preflight 始终使用单一代码版本。
- 只有 notice 或排障时才读 `references/session-preflight.md`。
- Dreaming 是默认关闭的独立旁路；普通 preflight 不检查、不启用也不提示 Dreaming。

首次无 `.kbconfig` 时，询问用户要上手引导还是常规建库。引导读 `TUTORIAL.md`；常规建库询问
父目录，默认创建 `byteworker_kb`。按 `docs/development/DESIGN.md` 初始化 8 类 knowledge 目录、sources、
raw_data、provenance、journal、reports、INDEX，并复制 context/todo 模板。KB 必须是无 remote
的独立本地 Git 仓库。

## Workflow 路由

`references/workflow-routes.json` 是可机器检查的加载闭包。确定意图后只加载对应 workflow 的
`required`，再按 source type/features 加条件文件；失败时才加载 `on_error`。不得用“普通流程”
代替显式闭包，也不得为了找一个命令整读其它能力。
隔离 worker 或动态输入接近上限时按 `references/workflow-budgets.md` 获取完整 token budget receipt；
超预算执行固定 action，不静默截断证据。

公共 CLI envelope 见 `references/machine-protocol.md`。统一使用
`bin/byteworker <tool> ...`；直接飞书 CLI 用 `bin/byteworker lark ...`，辅助脚本用
`bin/byteworker run ...`。Agent 不猜 Python、nvm、venv 或内部 CLI 路径。

### 按意图加载

- search：`references/command-search.md` + `references/citations.md`
- update：`references/command-update.md` + `references/conflict-policy.md` +
  `references/kb-mutation.md` + `references/write-rules.md`
- brief：`references/command-brief.md` + `references/command-search.md` +
  `references/citations.md`
- dashboard：`references/command-dashboard.md` + `references/kb-mutation.md` +
  `references/citations.md`
- context：`references/command-context.md` + `references/kb-mutation.md`
- thinking：`references/thinking.md` + `references/kb-mutation.md`
- todo：`references/todo.md`
- report：`references/report-scheduling.md` + `references/periodic-report.md` +
  `references/digest-routine.md` + `references/kb-mutation.md` + `references/citations.md`
- dreaming：`references/dreaming.md` + `references/dreaming-analysis.md` +
  `references/dreaming-consolidation.md` + `references/dreaming-actions.md` +
  `references/dreaming-reports.md` + `references/dreaming-review.md`；配置时加
  `references/dreaming-setup-guide.md`，首次启用再加
  `references/dreaming-onboarding.md`，maintenance job 加
  `references/dreaming-maintenance.md`；当前环境或目标宿主属于 TRAE 产品家族时加
  `references/dreaming-harness-trae.md`
- doctor：`references/doctor.md`

## Context

语义任务不再读取完整 `context.md`。先调用：

```bash
bin/byteworker context view --kb "<KB>" --intent "<intent>"
```

只消费该 intent 的固定章节投影。超过硬预算时先请用户归档过期信息。context 是用户真相源；
除用户明确要求的 context mutation 外，其它流程绝不改写。

## Digest

digest：

- `references/digest-core.md`
- `references/digest-dependencies.md`
- `references/digest-transaction.md`
- `references/provenance.md`
- `references/write-rules.md`
- `references/conflict-policy.md`

来源：飞书文档 `digest-doc.md`（评论加 `references/digest-comments.md`，白板只读取
结构 JSON 并加读 `digest-whiteboard.md`）；群聊 `digest-chat.md`；Meego `digest-meego.md`；Base
`digest-base.md`；风神 `digest-aeolus.md`；网页/本地资料 `digest-reading.md`；会议簇
`digest-meeting.md`；立场分析 `digest-analysis.md`；大型输入 `digest-large.md`；routine
`digest-routine.md`。Wiki 空间探索先读 `references/digest-wiki-space.md`，确认页面后按
feishu_doc；恢复任务还要读 `references/wiki-digest-jobs.md`。

标准无语义阶段按 `references/digest-flow.md` 统一走 `digest-flow`；`SourceBundle v2` 经
`digest-analysis-pipeline.md` / `digest-concurrency.md` 生成 `digest-plan/v2`。候选完成后运行
`digest-flow commit`（内部 execute 含锁内复验）；独立 `validate` 只用于失败排障，多来源用
`digest-batch-plan/v2`。Agent 做语义判断、冲突分类；事务负责 hash/schema/INDEX/journal/
commit/rollback；只认 `status=committed`。

标准路径由 `digest-flow` 自动建立 `run_id`、记录确定性阶段并结束终态；只有恢复或诊断时才按
`references/digest-observability.md` 手工调用底层日志命令。

事实 `[E1]` 绑raw，主记录设 `primary_source`。raw / Bundle 的 `source_title`
留原题；宽泛时，按来源可确认的作者、团队、项目/周期命名；不明标“归属待确认”。
person 用 `bin/resolve-users.sh --format json --jobs 4` 按 feishu_id 消解。

Meego/Base/风神/群聊先做 `source auth-status`。宿主注入的 user 凭据失效时，要求重新注入，
禁止用重复登录或静默切 bot 掩盖。其它未就绪先授权。`source inspect / capture` 仍 fail closed；
资源权限不足时请所有者共享。
结构化大视图保存完整快照，普通行不建节点，left_view 不等于删除。
查询具体记录用 `kb-query source-record`，不让 Agent 扫完整 raw。

大型输入 worker 和 Wiki resume page 必须从 workflow manifest 解析完整 digest 闭包；子 Agent
必须使用宿主提供的**全新隔离上下文**，prompt 自足且只传来源、确认范围、KB 和临时 artifact
路径，不得继承主对话。`fork_turns` 是 Codex adapter 的专有参数；其中
`fork_turns="all"` 表示继承全部历史，违反本流程，其他宿主不得被要求理解或伪造该参数。主
Agent 不重复语义分析、不主动轮询，只接收阶段状态和最终紧凑回执。

## 写入

- digest 只走 digest transaction。
- update/context/dashboard/report 只走 `byteworker-kb-mutation/v1`；thinking 使用 `update` operation。
- Todo 只走 Todo 工具。
- Agent 不直接执行 temp、INDEX、journal、git add/commit 或失败回滚。
- mutation 候选与 plan 放系统临时目录或 KB，不得进入 skill 仓库。
- knowledge mutation 必须按唯一 `conflict-policy.md` 声明 disposition；来源较新不等于可覆盖。
- area/org/person 的范围、目录证据、负责人和关系分型统一按 `references/write-rules.md`；digest
  与 update 的机器路由都必须包含它，不在常驻入口复制细则。

## 知识库检索回答引用(每次必做)

凡用户可见事实来自 KB，执行 `references/citations.md`：正文用 `[S<n>]` 绑定结论，末尾给出
原始出处、收录时间与置信度，并列原文时间/覆盖和版本/raw_id。不得只列节点/raw/report 路径；
缺失项明确披露。该规则覆盖 search、brief、dashboard、日报/周报及其回显。

## 报告、Todo 与 Doctor

自动日报/周报先完整 **routine digest**（不受七天限制），再按周期枚举主日历
`self_rsvp_status=accept` 日程；可访问的纪要、妙记转写和直接关联文档先 digest 后入报告，不建
routine、不递归、不 OAuth / 申请权限。mutation 保留手动备注，commit 后 complete。细则见
`references/report-calendar-meetings.md`。

Todo 以自然语言为主，内部 id 不要求用户记忆。digest 识别出的 Todo 只是候选，用户确认后才写。

Thinking 只在用户明确要求记录、保存、沉淀或更新认知时触发，普通讨论不自动保存。执行前读取
`references/thinking.md`；同一稳定主题持续更新一个 `thinking` 节点，状态仅允许
`effective` / `inactive`。纯对话思考不创建 raw，通过 mutation 原子维护节点、双向 links、
INDEX、journal 和本地回滚点。检索时标明它是用户当前思考，不能硬化为客观事实或正式决策。

doctor 默认只读调用 `bin/doctor.py` 对应 facade；交互请求只有用户明确要求才 fix。两个受控例外是
代码真实更新后的 postflight，以及用户已启用 Dreaming 后的 maintenance job：都只修 finding
明确声明的 auto_fix，并在共享写锁内失败回滚，不猜业务字段。

## Dreaming

Dreaming 是默认关闭的可选后台层。仅在用户明确要求设置、启用、调整、排障或前台单次分析时，
解析 manifest 的 `dreaming` 闭包及对应 `configure/enable/harness_trae/maintenance` feature；普通
preflight 和其它 workflow 不加载、不启用、不反复询问。全部授权、宿主、运行、Finding、Action、
报告和维护边界以这些按需 reference 为准；Dreaming 失败不得阻塞其它 workflow。

## 安全与架构

- skill 仓库只含通用逻辑；任何节点、raw、provenance、journal、INDEX、报告或候选业务内容都不
  得进入本仓库或外传。
- KB 禁止 remote/push；凭据只来自环境或仓库外权限文件，不得进入 URL、bundle、profile、raw、
  日志或命令参数。
- 不调用 lark-task；会议待办保存在 event，个人待办保存在 todo.md。
- 长流程只在真实阶段变化时给一行元信息状态；单阶段超过 60 秒可发一次 heartbeat，不粘贴业务
  原文、不为发状态主动轮询。大型 worker 由主 Agent 使用有界等待，避免主/子双重处理。

系统边界见[架构文档](docs/development/ARCHITECTURE.md)，schema 见
[存储设计](docs/development/DESIGN.md)。边界变化必须在**同一变更**同步文档和契约测试。
