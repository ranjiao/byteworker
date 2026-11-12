---
name: byteworker
description: 个人飞书工作知识库。摄取飞书文档、妙记、会议、群聊、Meego、多维表格、风神、网页和本地 Markdown，支持查询、更新、会前简报、看板、工作上下文、思考、自动日报周报、能力推荐、Dreaming 和兼容诊断。保存或查询工作资料、配置 Dreaming 或后台任务、检查知识库、排查运行或生成反馈时使用。
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
| context | 查看或维护全局工作上下文 |
| thinking | 保存或持续更新用户自己的自然语言认知与推演 |
| discover | 按当前知识库状态查看能力地图或推荐下一步 |
| dreaming | 用自然语言设置、启停或查看 Dreaming，默认关闭 |
| doctor | 检查或修复 schema/skill 兼容问题 |
| help | 原样展示 `references/help.md` |

用户直接描述任务时，按意图匹配。没有 `daily` / `weekly` 用户子命令。报告由宿主定时任务触发，也可以用自然语言要求补跑。

## 会话预检

交互会话首次使用本 skill 时，运行一次预检。

```bash
bin/byteworker preflight --interactive
```

定时任务、runner 和其他无人值守会话用 `preflight --unattended`。省略参数时也默认不产生 Tip。

访问尚未登记的飞书/Meego 来源时分别加 `--require feishu` / `--require meego`。

- 退出码为 0 且无输出时静默继续，不要再分别运行更新、依赖检查和报告状态命令。
- 有 JSON 输出时，只处理 `byteworker-session-preflight/v1.notices`，先解决 blocking 项。
- launcher 先完成更新检查，再加载 Python 模块，确保本次预检使用同一个版本。
- 只有 notice 或排障时才读 `references/session-preflight.md`。
- Dreaming 默认关闭，单独设置。普通 preflight 不检查、不启用也不提示 Dreaming。

## 交互语言

按顺序使用用户当前明确要求、KB 持久偏好、会话已确认语言。都没有时，用当前消息的语言询问一次，不默认英语。
已有库先运行 `context view --intent preferences` 读取 `interaction_language`，只补缺、不重建。
命令名、URL、原文不构成语言切换。用 context mutation 将持久偏好写入身份表「交互语言」。

## Workflow 路由

路由以 `references/workflow-routes.json` 为准。确定意图后用命令解析所需规则，避免整份加载。

```bash
bin/byteworker workflow-budget resolve --workflow <WORKFLOW> --source-type <TYPE>
```

无来源时省略 source-type。触发条件规则时加 `--feature <FEATURE>`，失败才加 `--include-on-error`。
已读文件无需重读。只补读明确触发的条件规则，避免沿解释性链接整包加载。
隔离 worker 或动态输入接近上限时，按 `references/workflow-budgets.md` 取完整 budget receipt。
超预算先按回执检查重复输入和分片。确需放宽时按预算细则合并询问，不自行提高上限。

用 `bin/byteworker <tool> ...` 调用命令。飞书 CLI 用 `bin/byteworker lark ...`，辅助脚本用
`bin/byteworker run ...`。由入口解析 Python、nvm、venv 和内部 CLI 路径。公共 CLI 响应格式见
`references/machine-protocol.md`。

### 按意图加载

- search：`references/command-search.md` + `references/citations.md`
- update：`references/command-update.md` + `references/conflict-policy.md` +
  `references/kb-mutation.md` + `references/write-rules.md`
- brief：`references/command-brief.md` + `references/command-search.md` +
  `references/citations.md`
- dashboard：`references/command-dashboard.md` + `references/citations.md`。
  查看调用 `dashboard view`，仅管理手动项时加载 `references/kb-mutation.md`。
- context：`references/command-context.md` + `references/kb-mutation.md`
- thinking：`references/thinking.md` + `references/kb-mutation.md`
- discover：`references/capability-discovery.md`
- report：`references/report-scheduling.md` + `references/periodic-report.md` +
  `references/digest-routine.md` + `references/kb-mutation.md` + `references/citations.md`
- dreaming：`references/dreaming.md` + `references/dreaming-analysis.md` +
  `references/dreaming-consolidation.md` + `references/dreaming-actions.md` +
  `references/dreaming-reports.md` + `references/dreaming-review.md`；配置时加
  `references/dreaming-setup-guide.md`，首次启用再加
  `references/dreaming-onboarding.md`，maintenance job 加
  `references/dreaming-maintenance.md`；当前环境或目标宿主是 TRAE 时加
  `references/dreaming-harness-trae.md`；process job 还必须从 manifest 加载
  `features.routine_digest` 先检查变化，再按来源解析普通 digest
- doctor：`references/doctor.md`
- 反馈或排查后台运行 → digest_feedback：`references/digest-feedback.md`
- search/brief/update/thinking/context/dashboard/report 的运行记录见 `references/operation-trace.md`。

## 能力发现

真人交互中，digest/search/update/brief/dashboard/context/thinking、来源登记、报告和 Viewer
请求成功后，最终回复前**必须**执行公共收尾，不先判断是否需要推荐：

```bash
bin/byteworker discover complete --kb "<KB>" --interactive --after <capability>
```

报告映射 `reports`，来源登记映射 `routine`，其它同名。只由主 Agent 每个用户请求调用一次；
失败、等待用户裁决、隔离 worker、help/discover/doctor/dreaming 不执行。返回 suggestion 时在
结果后附一句 message 和 example；null 静默。完整规则见 `references/capability-discovery.md`。
preflight 建议只作候选，收尾重新选择，不重复展示或写 shown；语义信号须有当前对话证据。
无人值守 session 禁止调用 complete/recommend 或展示 Tip；CLI 无交互标记也不推荐、不记使用。
Dreaming 固定为 `explicit_only`，不进入通用推荐，仍只由用户明确意图触发完整导览。

## Context

语义任务只读取当前意图需要的上下文。先调用：

```bash
bin/byteworker context view --kb "<KB>" --intent "<intent>"
```

只使用命令返回的固定章节。超过硬预算时，先请用户归档过期信息。context 以用户提供的信息为准，
仅在用户明确要求修改时走 context mutation。

## Digest

digest 先用 resolve 找齐并加载该来源所需的规则，包括完整的安全与语义 policy。出处按 `references/provenance.md` 处理。

来源规则由 resolve 选择，飞书正文和全部评论（`references/digest-comments.md`）均必读；白板只取结构 JSON 并加 whiteboard。
会议簇、立场、大型输入、routine 按对应来源/feature 加载；Wiki 空间走 wiki_space（`references/digest-wiki-space.md`），
确认页面后按 feishu_doc；恢复任务还要读 `references/wiki-digest-jobs.md`。

按 `references/digest-flow.md` 编排、`references/digest-inline.md` 交接，复用 prepare 的 packet/plan。
planner 选择 parallel 才加 parallel_execution，读 `digest-analysis-pipeline.md` / `digest-concurrency.md`；
多来源原子提交加 batch，读 `references/digest-transaction.md`；内部资料加 internal_reading。
候选完成后只运行 `digest-flow commit`，由它在锁内复验。独立 `validate` 只用于失败排障。
多来源用 `digest-batch-plan/v2`。Agent 负责语义判断、冲突分类，事务负责确定性写入和回滚。
仅 `status=committed` 表示写入成功。

flow 自动记录确定性阶段与终态；语义打点和等待按 inline。恢复、诊断或 usage 上报加 recovery
读取 `references/digest-observability.md`。

事实 `[E1]` 绑 raw，主记录设 `primary_source`。raw / Bundle 的 `source_title`
留原题；宽泛时，按来源可确认的作者、团队、项目/周期命名；不明标“归属待确认”。
person 用 `bin/resolve-users.sh --format json --jobs 4` 按 feishu_id 消解。

Meego/Base/风神/群聊先做 `source auth-status`。宿主注入的 user 凭据失效时，要求重新注入，
禁止用重复登录或静默切 bot 掩盖。其它未就绪先授权。`source inspect / capture` 仍 fail closed；
资源权限不足时请所有者共享。
大型结构化视图保存完整快照，不为普通行建节点。left_view 只表示记录已离开视图。
查询具体记录时用 `kb-query source-record`，禁止 Agent 扫描完整 raw。

大型输入 worker 和 Wiki resume page 必须从 workflow manifest 解析完整 digest 闭包；子 Agent
必须使用宿主提供的**全新隔离上下文**，prompt 自足且只传来源、确认范围、KB 和临时 artifact
路径，不得继承主对话。`fork_turns` 是 Codex adapter 的专有参数；其中
`fork_turns="all"` 表示继承全部历史，违反本流程，其他宿主不得被要求理解或伪造该参数。主
Agent 不重复语义分析、不主动轮询，只接收阶段状态和最终紧凑回执。

## 写入

- digest 只走 digest transaction。
- update/context/report 和 dashboard 手动设置写入只走 `byteworker-kb-mutation/v1`；thinking 使用 `update` operation。
- Agent 不直接执行 temp、INDEX、journal、git add/commit 或失败回滚。
- mutation 候选与 plan 放系统临时目录或 KB，不得进入 skill 仓库。
- knowledge mutation 必须按唯一 `conflict-policy.md` 声明 disposition；来源较新不等于可覆盖。
- 所有知识写入先过 `references/write-rules.md` 的「信息增量门槛」；主记录也不能写运行流水、
  重复摘要或归档占位说明。保存新快照与新增业务知识分别判断。
- area/org/person 的范围、目录证据、负责人和关系分型统一按 `references/write-rules.md`；digest
  与 update 的机器路由都必须包含它，不在常驻入口复制细则。

## 知识库检索回答引用(每次必做)

按 `references/citations.md` 给 KB 回答逐条标 `[S<n>]`。列出原始出处、收录时间与置信度，
并列出原文时间或覆盖范围、版本和已知缺口。按引用规则提供 raw_id。节点日期只作元数据，事实时效以原始证据为准。

## 报告与 Doctor

自动日报/周报先完整 **routine digest**（不受七天限制），再按周期枚举主日历
`self_rsvp_status=accept` 日程；可访问的纪要、妙记转写和直接关联文档先 digest 后入报告，不建
routine、不递归、不 OAuth / 申请权限。mutation 保留手动备注，commit 后 complete。细则见
`references/report-calendar-meetings.md`。

Thinking 只在用户明确要求记录、保存、沉淀或更新认知时触发，普通讨论不自动保存。执行前读取
`references/thinking.md`；同一稳定主题持续更新一个 `thinking` 节点，状态仅允许
`effective` / `inactive`。纯对话思考不创建 raw，通过 mutation 原子维护节点、双向 links、
INDEX、journal 和本地回滚点。检索时标明它是用户当前思考，不能硬化为客观事实或正式决策。

doctor 默认只读调用 `bin/doctor.py` 对应 facade；交互请求只有用户明确要求才 fix。两个受控例外是
代码真实更新后的 postflight，以及用户已启用 Dreaming 后的 maintenance job：都只修 finding
明确声明的 auto_fix，并在共享写锁内失败回滚，不猜业务字段。

## Dreaming

Dreaming 可以在后台定期检查已授权的信息，默认关闭。用户说「帮我配置 Dreaming」或明确要求启用时，先读取
`references/dreaming.md`、`references/dreaming-onboarding.md` 和
`references/dreaming-setup-guide.md`。用户说「帮我配置后台信息助手」「设置自动信息分析」
「每天汇总重要信息」「重要风险及时提醒」「调整后台频率/范围」「为什么没有自动运行/摘要」
或「继续设置」时，也进入设置向导。
优先使用 Codex / TRAE 的结构化选项控件；不可用时使用编号选项。

面向用户称为 Dreaming，具体选项用「自动检查」「待关注事项」「定时摘要」「紧急提醒」「本地定时任务」等
自然语言。设置时不直接展示 `operational`、`persist_report`、`instant_alert`、`harness`、grant、
Finding 或 job。先读取 status，从未完成的步骤继续。只修改一项时，只问该项及其依赖。
用自然语言汇总选择，用户确认后再写配置。完成后只展示 `dreaming-setup-guide.md` 定义的用户状态卡。

首次启用须完整介绍它与 digest 的差异、能力、默认授权、隐私、成本、机器条件、维护和退出方式。
请用户配置并确认完整 schedule。导览、schedule 和机器条件都明确确认后，才能传
`--acknowledge-capability-tour --acknowledge-schedule --acknowledge-machine-runtime` 并创建宿主 local 任务。

- 普通安装、升级、preflight 和其它命令不得自动启用或反复询问。
- process 支持按分钟间隔、每天固定时间、每 N 天固定时间；不得静默套用默认频率。先运行
  `dreaming configure`，回显所有 job、timezone、日志保留期和 `next_due_at`，再询问确认。
- 宿主任务创建后，必须用 `dreaming harness register` 登记。只有 `harness.status=installed` 且
  `operational=true` 才能告知用户后台已配置完成。宿主无本地定时能力时只提供前台单次分析。
- 检测到 TRAE / TRAE SOLO / TraeWork 环境时必须加载 `references/dreaming-harness-trae.md`。
  当前会话没有 Schedule 工具时，提示用户在 TRAE 任务面板创建本地 Code 定时任务、每 30 分钟
  执行 runner，并先 Run now；没有真实任务和首次触发证据时保持 harness pending，禁止猜私有
  API、改 TRAE 配置或用 cron/launchd 冒充 Agent task。
- 每次 lease 都有稳定 `run_id`。runner 在采集、分析、整合、动作、报告、维护和恢复等长阶段
  调用 `dreaming heartbeat`，最终 `complete`；用户可用 `dreaming runs list/show/tail` 查询
  `0600` 结构化运行日志。日志只含阶段、耗时、计数、error code 和 artifact path，不含 IM 正文。
- 宿主回显诊断编号和摘要。后台先自动处理，普通缺口不等待答复。异常按
  `references/dreaming.md` 查最近唤醒和运行，不要求用户提供编号。
- 启用默认不接管现有日报/周报；接管需要单独确认旧 scheduler owner 已释放。
- Dreaming local state 使用 `byteworker-dreaming/v2`；读取已有 v1 时由确定性状态层先写本地私密
  备份再迁移。迁移失败保持 Dreaming 关闭，不得阻塞其它命令或让 Agent 手改 state JSON。
- IM grant 默认 `off`。`all_visible` 会读取 P2P 和免打扰会话，只有用户明确确认后才可设置；
  `process prepare` 只生成私密 EvidenceBatch，不调用模型、不写 Finding/报告/知识。
- `process prepare` 后的 Agent 分析必须读 `references/dreaming-analysis.md`；Finding 持久与去重
  规则见 `references/dreaming-consolidation.md`。Agent 不直接编辑 Finding history/projection。
- 后台 process 按 `references/dreaming-routine.md` 检查全部启用来源，逐源核验覆盖。
  Meego 按快照差异分析；核验宿主错误后自动缩片并逐片保存。无法继续时暂缓该来源。
  完整报告仍须满足全部覆盖条件。
- 自动 IM 补跑只看最近 14 天，历史窗口可按需前台处理。
- 任何报告、来源或知识动作必须读 `references/dreaming-actions.md`，先 plan/claim 并在
  下游调用前 validate-claim；不得绕过 Ledger 或自行重试 reconcile action。
- Dreaming owner 下的 morning/daily/weekly 按 `references/dreaming-reports.md` 消费 process
  已提交的普通 digest、committed Finding 和 KB；报告 job 自身不得重复 routine digest，也不得
  与旧 report automation 双 owner。
- maintenance job 按 `references/dreaming-maintenance.md` 调用公开 doctor facade，只执行获准的
  确定性修复。人工待办逐项保留，只汇总新增或变化项，后续扫描继续运行。
- 前台单次处理、Finding review/explain/feedback 和私有 shadow 评估按
  `references/dreaming-review.md`；foreground 不得隐式启用后台或持久化 Finding。
- 用户明确要求分析今天、昨天或指定窗口的 IM 时，使用 foreground
  `dreaming process once --source im`；独立 Inbox 已移除，旧命令只返回 `INBOX_REMOVED`。
- Dreaming 只通过公开 `bin/byteworker` 命令调用既有能力，不 import digest/query 内部模块。
- Dreaming 禁用、失败或状态损坏不能阻塞 digest/search/update。

## 安全与架构

- skill 仓库只含通用逻辑；任何节点、raw、provenance、journal、INDEX、报告或候选业务内容都不
  得进入本仓库或外传。
- KB 禁止 remote/push；凭据只来自环境或仓库外权限文件，不得进入 URL、bundle、profile、raw、
  日志或命令参数。
- 不调用 lark-task；会议中的原始行动项保存在 event。
- 长流程只在真实阶段变化时给一行元信息状态；单阶段超过 60 秒可发一次 heartbeat，不粘贴业务
  原文、不为发状态主动轮询。大型 worker 由主 Agent 使用有界等待，避免主/子双重处理。

系统边界以 [`docs/development/ARCHITECTURE.md`](docs/development/ARCHITECTURE.md) 为准，
schema 以 `docs/development/DESIGN.md` 为准。模块、依赖、信息流、失败边界或成功判定变化时，**同一变更**同步架构文档和契约测试。

失败恢复见 `references/error-recovery.md`。
