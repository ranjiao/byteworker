---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、妙记、会议、群聊、Meego 保存视图、飞书多维表格视图、风神看板、外部 blog/论文/wiki、本地 md 消化成结构化实体图，并保存和持续更新用户自己的自然语言思考，支持查询、更新、会前简报、看板、自然语言 Todo、自动日报/周报、IM Inbox、全局工作上下文和兼容诊断。当用户要保存或查询工作资料、沉淀自己的思考、管理待办提醒、生成工作报告、分析飞书 IM、检查知识库，或使用 /byteworker digest/search/update/brief/dashboard/todo/inbox/context/thinking/doctor/help 时触发。
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
| inbox | 扫描飞书 IM 高信号事项 |
| context | 查看或维护全局工作上下文 |
| thinking | 保存或持续更新用户自己的自然语言认知与推演 |
| doctor | 检查或修复 schema/skill 兼容问题 |
| help | 原样展示 `references/help.md` |

自然语言未写子命令时按意图匹配。没有 `daily` / `weekly` 用户子命令；自动报告由宿主任务或
自然语言补跑触发。

## 每个 Session 先做

首次使用本 skill 时只运行一次：

```bash
bin/byteworker preflight
```

访问尚未登记的飞书/Meego 来源时分别加 `--require feishu` / `--require meego`。

- 无输出且退出码 0：静默继续，**不要再分别运行** update-check、依赖检查、Todo check 或报告状态。
- 输出 JSON：只处理 `byteworker-session-preflight/v1.notices`；blocking 先解决。
- launcher 在加载 Python 模块前完成更新，因此本次 preflight 始终使用单一代码版本。
- 只有 notice 或排障时才读 `references/session-preflight.md`。

首次无 `.kbconfig` 时，询问用户要上手引导还是常规建库。引导读 `TUTORIAL.md`；常规建库询问
父目录，默认创建 `byteworker_kb`。按 `DESIGN.md` 初始化 8 类 knowledge 目录、sources、
raw_data、provenance、journal、reports、INDEX，并复制 context/todo 模板。KB 必须是无 remote
的独立本地 Git 仓库。

## Workflow 路由

`references/workflow-routes.json` 是可机器检查的加载闭包。确定意图后只加载对应 workflow 的
`required`，再按 source type/features 加条件文件；失败时才加载 `on_error`。不得用“普通流程”
代替显式闭包，也不得为了找一个命令整读其它能力。

公共 CLI envelope 见 `references/machine-protocol.md`。统一使用
`bin/byteworker <tool> ...`；直接飞书 CLI 用 `bin/byteworker lark ...`，辅助脚本用
`bin/byteworker run ...`。Agent 不猜 Python、nvm、venv 或内部 CLI 路径。

### 按意图加载

- search：`references/command-search.md` + `references/citations.md`
- update：`references/command-update.md` + `references/conflict-policy.md` +
  `references/kb-mutation.md`
- brief：`references/command-brief.md` + `references/command-search.md` +
  `references/citations.md`
- dashboard：`references/command-dashboard.md` + `references/kb-mutation.md` +
  `references/citations.md`
- context：`references/command-context.md` + `references/kb-mutation.md`
- thinking：`references/thinking.md` + `references/kb-mutation.md`
- todo：`references/todo.md`
- report：`references/report-scheduling.md` + `references/periodic-report.md` +
  `references/digest-routine.md` + `references/kb-mutation.md` + `references/citations.md`
- inbox：`references/im-inbox-summary.md` + `references/kb-mutation.md` +
  `references/citations.md`
- doctor：`references/doctor.md`

## Context

语义任务不再读取完整 `context.md`。先调用：

```bash
bin/byteworker context view --kb "<KB>" --intent "<intent>"
```

只消费该 intent 的固定章节投影。超过硬预算时先请用户归档过期信息。context 是用户真相源；
除用户明确要求的 context mutation 外，其它流程绝不改写。

## Digest

标准 digest 的公共闭包必须包含：

- `references/digest-core.md`
- `references/digest-dependencies.md`
- `references/digest-transaction.md`
- `references/provenance.md`
- `references/write-rules.md`
- `references/conflict-policy.md`

按来源加读：飞书文档 `digest-doc.md`（评论加 `references/digest-comments.md`，白板只读取
结构 JSON 并加读 `digest-whiteboard.md`）；群聊 `digest-chat.md`；Meego `digest-meego.md`；Base
`digest-base.md`；风神 `digest-aeolus.md`；网页/本地资料 `digest-reading.md`；会议簇
`digest-meeting.md`；立场分析 `digest-analysis.md`；大型输入 `digest-large.md`；routine
`digest-routine.md`。Wiki 空间探索先读 `references/digest-wiki-space.md`，确认页面后按
feishu_doc；恢复任务还要读 `references/wiki-digest-jobs.md`。

来源先产生 `byteworker-source-bundle/v2`，Agent 再生成只引用 bundle 的
`digest-plan/v2` 和完整候选节点。标准路径先运行 `bin/digest-txn.py preflight`；候选完成后
直接运行 `execute`，由它在写入前完成完整 validate 与锁内复验。独立 `validate` 只用于失败排障。
两个以上来源共同更新
节点时用 `digest-batch-plan/v2`。语义判断、冲突分类、实体取舍和候选正文由 Agent 负责；
hash、幂等、schema、INDEX、journal、精确 commit 和 rollback 由事务负责。只有
`status=committed` receipt 表示写入成功。

关键事实使用 `[E1]` 等标记映射到 raw anchor，主记录声明 `primary_source`。新建/更新 person
时运行 `bin/resolve-users.sh --format json`，按 feishu_id 消解并同步可见通讯录字段；空查询不
清除旧值。

Meego/Base/风神/群聊先调用 `source auth-status`。未就绪时告诉用户并取得登录授权；运行时
`source inspect / capture` 仍 fail closed。资源 Permission Denied 请求所有者共享，**禁止用重复登录或静默切 bot 掩盖**。
结构化大视图保存完整快照，普通行不建节点，left_view 不等于删除。
查询具体记录用 `kb-query source-record`，不让 Agent 扫完整 raw。

大型输入 worker 和 Wiki resume page 必须从 workflow manifest 解析完整 digest 闭包；子 Agent
必须显式使用 `fork_turns="none"`，prompt 自足且只传来源、确认范围、KB 和临时 artifact 路径，
不得继承主对话。主 Agent 不重复语义分析、不主动轮询，只接收阶段状态和最终紧凑回执。

## 写入

- digest 只走 digest transaction。
- update/context/dashboard/thinking/report/inbox 只走 `byteworker-kb-mutation/v1`。
- Todo 只走 Todo 工具。
- Agent 不直接执行 temp、INDEX、journal、git add/commit 或失败回滚。
- mutation 候选与 plan 放系统临时目录或 KB，不得进入 skill 仓库。
- knowledge mutation 必须按唯一 `conflict-policy.md` 声明 disposition；来源较新不等于可覆盖。
- 新建或更新 `area` 主题领域节点时，标题和概述必须显式写出业务、团队或个人限定语；同一主题在
  不同业务中的节奏、指标、技术判断与共识分别保存，不得合并成看似公司级的通用方法论。无法从
  来源确认归属时先保留为 reading/project 并披露边界，不创建宽泛 `area`。
- 新建或更新内部 `org` 时，名称优先使用飞书通讯录返回的完整正式部门路径；通过
  `resolve-users.sh --format json` 的实时结果与已有 person 的 `department_path` 交叉核对，不按
  口语简称或路径片段臆造组织。组织负责人必须来自用户确认或明确权威来源，不能从成员、职级、
  文档作者或会议角色推断；未确认时询问用户并显式标记“待用户确认”。人员的**通讯录当前归属**、
  **管理职责**和**汇报关系**是三类独立事实，分别记录来源与日期：用户确认某人负责更细组织或
  向某人汇报时，不得据此伪造或覆盖较粗的 `department_path`；目录只返回祖先路径、与管理职责
  不同或暂未更新时并列披露。用户给出账号简称、异体姓名或英文名时先用通讯录与 `feishu_id`
  对齐已有 person，唯一命中才复用，禁止创建重复人物。项目协作、会议同现和历史链接不证明当前
  成员关系或组织层级；用户纠正归属时修正当前 TL;DR、基本信息和 links，同时把旧关系作为带日期
  的历史协作保留。

## 知识库检索回答引用(每次必做)

凡用户可见事实来自 KB，执行 `references/citations.md`：正文用 `[S<n>]` 绑定结论，末尾给出
原始出处、收录时间与置信度，并列原文时间/覆盖和版本/raw_id。不得只列节点/raw/report 路径；
缺失项明确披露。该规则覆盖 search、brief、dashboard、日报/周报、IM 报告及其回显。

## 报告、Inbox、Todo 与 Doctor

自动日报/周报每次先运行完整 **routine digest**，不受 `.last-routine-digest` **七天**提醒限制；
只重放所有已登记且启用的来源，不新增来源、不扩大范围、不发起 OAuth。报告候选通过 mutation
保留“手动补充 / 备注”，commit 后才调用 report-automation complete。调度细则见
`references/report-scheduling.md`。

Inbox 只把本地筛选后的 top threads 交给模型，按 `im-inbox-summary.md` 的评分锚点、阈值和
reason codes 输出；先验证 semantic result，再通过 mutation 保存摘要。全量 IM 原文不入库。

Todo 以自然语言为主，内部 id 不要求用户记忆。digest 识别出的 Todo 只是候选，用户确认后才写。

Thinking 只在用户明确要求记录、保存、沉淀或更新认知时触发，普通讨论不自动保存。执行前读取
`references/thinking.md`；同一稳定主题持续更新一个 `thinking` 节点，状态仅允许
`effective` / `inactive`。纯对话思考不创建 raw，通过 mutation 原子维护节点、双向 links、
INDEX、journal 和本地回滚点。检索时标明它是用户当前思考，不能硬化为客观事实或正式决策。

doctor 默认只读调用 `bin/doctor.py` 对应 facade；只有用户明确要求才 fix。代码真实更新后的
postflight 只修明确 auto_fix，并在共享写锁内失败回滚，不猜业务字段。

## 安全与架构

- skill 仓库只含通用逻辑；任何节点、raw、provenance、journal、INDEX、报告或候选业务内容都不
  得进入本仓库或外传。
- KB 禁止 remote/push；凭据只来自环境或仓库外权限文件，不得进入 URL、bundle、profile、raw、
  日志或命令参数。
- 不调用 lark-task；会议待办保存在 event，个人待办保存在 todo.md。
- 长流程只在真实阶段变化时给一行元信息状态；单阶段超过 60 秒可发一次 heartbeat，不粘贴业务
  原文、不为发状态主动轮询。大型 worker 由主 Agent 使用有界等待，避免主/子双重处理。

系统边界以 [`ARCHITECTURE.md`](ARCHITECTURE.md) 为准，schema 以 `DESIGN.md` 为准。修改模块、
依赖、信息流、失败边界或成功判定时，必须在**同一变更**同步架构文档和契约测试。
