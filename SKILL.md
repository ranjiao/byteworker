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

TRAE IDE（含 SOLO）用 `preflight --interactive --skip-report-automation`，跳过自动报告设置检查。
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

search、brief、update、thinking、context、dashboard 和手动报告按 `references/operation-trace.md` 记录本次请求。
报告按 `references/report-scheduling.md`，Doctor 按 `references/doctor.md`。反馈或排障使用 digest_feedback。

## 能力发现

真人请求成功后，digest、search、update、brief、dashboard、context、thinking、来源登记、报告和 Viewer 的主 Agent 按 `references/discovery-completion.md` 调用一次 `discover complete`；
只在返回 suggestion 时展示。无人值守、worker、等待用户或失败时跳过；无人值守不推荐、不展示 Tip。
Dreaming 只由用户明确触发。

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

按 prepare 的 packet/plan 执行；并行加 parallel_execution，多来源原子提交加 batch，内部资料加 internal_reading。
只走 `digest-flow commit` 写入，失败或恢复用 recovery。隔离 worker 的宿主约束见对应路由。

事实 `[E1]` 绑 raw，主记录设 `primary_source`。raw / Bundle 的 `source_title`
留原题；宽泛时，按来源可确认的作者、团队、项目/周期命名；不明标“归属待确认”。
person 用 `bin/resolve-users.sh --format json --jobs 4` 按 feishu_id 消解。

Meego/Base/风神/群聊先做 `source auth-status`。宿主注入的 user 凭据失效时，要求重新注入，
禁止用重复登录或静默切 bot 掩盖。其它未就绪先授权。`source inspect / capture` 仍 fail closed；
资源权限不足时请所有者共享。
大型结构化视图保存完整快照，不为普通行建节点。left_view 只表示记录已离开视图。
查询具体记录时用 `kb-query source-record`，禁止 Agent 扫描完整 raw。

大型输入 worker 和 Wiki resume page 解析对应路由，必须使用宿主提供的全新隔离上下文，以路径交接，禁止继承主对话。

## 写入

- digest 只走 digest transaction。
- update/context/report 和 dashboard 手动设置写入只走 `byteworker-kb-mutation/v1`；thinking 使用 `update` operation。
- Agent 不直接执行 temp、INDEX、journal、git add/commit 或失败回滚。
- mutation 候选与 plan 放系统临时目录或 KB，不得进入 skill 仓库。
- knowledge mutation 必须按唯一 `conflict-policy.md` 声明 disposition；来源较新不等于可覆盖。
- 所有知识写入先过 `references/write-rules.md` 的「信息增量门槛」；主记录也不能写运行流水、
  重复摘要或归档占位说明。已有来源无业务增量用 snapshot_only 仅归档，节点保持不变。
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

默认关闭。查看状态用 `dreaming status`。配置与调整用 configure，首次启用用 enable；
读取现有设置、解释能力与成本、确认计划和机器条件后才启用。后台真正可用须确认宿主已登记且 operational。
Trae 定时任务加 trae_schedule。排障、停用和迁移见 operations，接管已有报告须单独确认。

已领取的后台任务按 runner 选 feature：maintenance 只调用 Doctor，process 用 routine_digest，
morning/daily/weekly 用 report，recovery 用 recovery。由 Dreaming 接管的指定周期补报也用 report，先 start 再 prepare / complete。
用户明确要单次分析某段 IM 时用 review 的 foreground 入口；复核和评估同样用 review。
来源、报告或知识动作须依 actions 走授权与回执。IM 授权默认关闭，扩大范围须明确确认。
飞书摘要恢复先走 report delivery-check；99992361 等待按发送应用重新解析已确认收件人，
不默认当前登录账号。configure 只修未来投递；原 pending 条目通过 report repair-recipient 按 ID 修正。

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
