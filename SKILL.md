---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、会议妙记、会议、群聊、Meego 保存视图、飞书多维表格视图、风神看板、外部 blog/论文/wiki、本地 md 摄取(digest)并消化成结构化实体图笔记(人员/项目/主题领域/组织/事件/决策/读物),支持对话式查询(search)、更新(update)、会前简报(brief)、工作看板(dashboard)、自然语言待办与提醒(todo)、由宿主定时任务自动生成日报/周报、IM Inbox 摘要(inbox)、对话式维护全局工作上下文(context),以及扫描/修复知识库与当前 skill/schema 不兼容问题(doctor)。当用户要把资料存入知识库、定期复查 Meego / 多维表格视图 / 风神看板、查询或更新工作知识、设置或补跑自动日报/周报、分析飞书 IM、查看工作看板、检查/修复知识库、升级 skill 后排查数据兼容性,或说“记个待办”“明天/后天/下周六提醒我”“刚才那个做完了”“延期/取消提醒”“看看还有什么没做”时使用;支持 /byteworker digest/search/update/brief/dashboard/todo/inbox/context/doctor/help 子命令,但 todo 日常以自然语言为主。
---

# byteworker 个人知识库

把日常飞书工作信息消化成结构化的**实体图知识库**,供对话式查询与更新。

> **用法**:`/byteworker 子命令 [参数]`(如 `/byteworker digest 飞书URL`),或直接自然语言。
> 不清楚怎么用就 `/byteworker help`。

## 子命令

| 子命令 | 中文 | 作用 | 示例 |
|--------|------|------|------|
| `digest` | 摄取 | 把飞书文档/妙记/会议/群聊/Meego/Base/风神/md 消化入库 | `/byteworker digest <飞书URL>` |
| `search` | 查询 | 问知识库 | `/byteworker search 我们关于X定过什么` |
| `update` | 更新 | 某条知识有新进展 | `/byteworker update Y项目有进展` |
| `brief` | 会前简报 | 开会前拉相关上下文 | `/byteworker brief` |
| `dashboard` | 工作看板 | 看当下该关注什么 | `/byteworker dashboard` |
| `todo` | 待办提醒 | 用自然语言增加、完成、延期、取消或查看待办 | `明天下午三点提醒我提交周报` |
| `inbox` | IM摘要 | 扫描飞书 IM 高信号消息并生成摘要 | `/byteworker inbox 昨天` |
| `context` | 全局上下文 | 对话式增删改你的工作上下文 | `/byteworker context 我的当前重点改成X` |
| `doctor` | 兼容诊断 | 扫描知识库与当前 schema 的不匹配并做确定性修复 | `/byteworker doctor` |
| `help` | 帮助 | 用法说明 | `/byteworker help` |

带子命令时按对应能力执行;未带子命令的自然语言输入,按各能力的「触发」判断。

## 操作前必读

**统一 session preflight(最先做且只做一次)**:每个新 session 第一次使用本 skill 时运行
`bin/byteworker preflight`。本次明确要访问尚未登记的飞书来源时加 `--require feishu`，访问 Meego
时加 `--require meego`；已登记的来源会从知识库 `sources/` 自动推导，不用重复声明。

- **无输出且退出码 0** → 全部健康，静默继续；不要再分别运行 `update-check`、`check-deps`、
  `report-automation status`、Todo `init/check`，也不要向用户解释这些内部逻辑。
- **有一行 JSON 输出** → 只处理 `notices`：`severity=blocking` 先修复再做依赖它的业务；
  普通 notice 在当前回答合适位置简短转告。`TODO_REMINDERS` 展示后仍按 `references/todo.md`
  调用 `mark-reminded`；自动报告 onboarding / prompt upgrade 在完成当前请求后询问。
- preflight 负责定位知识库、解析可用 Python/Node/lark-cli/meegle、静默自动更新、验证
  `context.md` / `todo.md`、检查 Todo 和自动报告设置状态。它只返回异常和待处理事项，不把健康
  检查细节塞进 Agent context。只有出现 notice、用户主动检查更新或排查 runtime 时才读
  [`references/session-preflight.md`](references/session-preflight.md)；健康路径不要加载它。

**机器协议(确定性 CLI 必用)**:Agent 或其它程序调用 digest 事务、查询、doctor、Todo、
自动报告状态/租约、provenance 回填时,按 [`references/machine-protocol.md`](references/machine-protocol.md)
通过 runtime-safe launcher `bin/byteworker <tool> ...` 调用并解析统一的
`status / data / error / context` JSON envelope。`bin/digest-txn.py`、`bin/kb-query.py`、
`bin/doctor.py`、`bin/todo.py` 等直接入口继续保留给人工排障和兼容旧调用。
直接调用飞书 CLI 用 `bin/byteworker lark ...`；执行依赖 Node/Python/PATH 的辅助脚本用
`bin/byteworker run <command> ...`，不要猜 nvm、venv 或 Python 绝对路径。

Meego / Base / 风神 / 群聊来源授权先用 `source auth-status` 做无副作用检查。未就绪时必须先告诉用户将发起
OAuth 并取得同意：Meego 走独立 `meegle` 登录；Base 走 `lark-cli` 用户登录 + 最小只读
scopes 的 split-flow（原样 URL + 二维码，后续由 agent 用 device code 收尾）；风神由
byteworker 原生只读客户端读取，凭据只从环境变量或仓库外 `0600` 私密文件注入。运行时
`source inspect / capture` 也会执行同一 Auth Guard。资源级 Permission Denied / `91403`
应请求所有者共享，禁止用重复登录或静默切 bot 掩盖。

Meego 空间主页 URL 不直接 digest、不遍历空间，也不尝试搜索或用页面自动化发现视图；直接提醒
用户提供包含 `/storyView/<view_id>` 的具体 Story View 页面 URL，收到明确视图后才进入
inspect / capture。Meego / Base / 风神大视图统一采用“完整快照 +
稳定记录 ID 差异 + 知识晋升门槛”：raw 保留完整 snapshot，`source diff` 只缩小语义复核范围；
普通行不建节点，`left_view` 不等于删除，只有长期
项目、明确决策、时间事件或稳定跨记录主题才进入实体图。查询其中的具体记录时必须用机器协议
调用 `kb-query source-record`，禁止 Agent 直接扫描完整大 raw。细则见对应
`references/digest-*.md` 与 `references/commands.md`。

**本 skill 只含 agent 逻辑。业务数据(知识库内容)存在独立的「知识库数据目录」,不在本 skill 仓库内,也不进本仓库的 git。**

- 读本 skill 目录下的 `.kbconfig`(已 gitignore),其中一行是知识库数据目录的绝对路径。
- 若 `.kbconfig` 不存在(**首次使用**):
  - **先问用户要不要走「上手引导」** —— 一句话:「看来是第一次用 byteworker,要不要花
    2-4 分钟完成建库、个人信息与关注重点，并设置自动日报 / 周报？摄取和查询演示可以跳过。」
    - **同意** → 读本 skill 目录下的 [`TUTORIAL.md`](TUTORIAL.md),按其剧本带用户走;引导**内含建库**那一步,走完即转入正常使用,不必再走下面的「常规首次设置」。
    - **跳过** → 走「常规首次设置」。
  - **常规首次设置**:**主动询问用户**知识库数据目录放在哪里 —— 让用户给一个父目录,目录名默认 `byteworker_kb`(用户可改);拼出绝对路径后写入 `.kbconfig`。
- 用户之后想再看引导(说「跑一下上手引导」「重看教程」等)→ 读 `TUTORIAL.md` 重走一遍(`.kbconfig` 已存在则跳过其中的建库步骤)。
- 若该数据目录不存在或为空:按 DESIGN.md §1.B 初始化 —— 创建 `knowledge/` 的 7 个子目录、`sources/`、`raw_data/`、`provenance/`、`journal/`、`reports/daily/`、`reports/weekly/`、`reports/im/`、空 `INDEX.md`,并把 skill 目录的 `templates/context.md` / `templates/todo.md` 整份复制为数据目录的 `context.md` / `todo.md`;再对该数据目录执行 `git init`(**仅本地、永不配 remote**,作误删/错改的回滚网)。
- **下文所有 `knowledge/`、`raw_data/`、`provenance/`、`journal/`、`reports/`、`INDEX.md`、`dashboard.md`、`context.md`、`todo.md` 路径,一律指知识库数据目录下的对应路径;`templates/` 与 `DESIGN.md` 在本 skill 目录下。**

**自动报告设置与升级迁移**:统一 preflight 已检查
`report-automation status --kb <知识库路径>`。返回 onboarding / prompt upgrade notice 时先完成
当前业务，再读 [`references/report-scheduling.md`](references/report-scheduling.md) 处理一次性
询问、宿主真相源和 local-only 边界；健康时不要加载调度细则。

**定期摄取到期提醒**:本次操作若会读 `INDEX.md`,顺带看「定期摄取清单」—— 若清单非空、且数据目录的 `.last-routine-digest`(记上次「定期摄取」运行日期;文件不存在 = 从未运行)距今 ≥7 天 → 用一句话提醒用户「定期摄取清单有 N 项可能该查更新了,需要就说『跑定期摄取』」。**只提醒,不打断当前请求、不自动跑。**

**全局上下文(语义任务必读)**:preflight 只验证知识库 `context.md` 存在；执行 digest / search /
update / brief / dashboard / todo 或生成报告时再读一次内容。help、纯 doctor、设置调度等不依赖
用户语义的操作无需把全文载入 context。文件固定包含使用者身份、职责范围、当前重点、主管方向、
当前约束、交互与提醒偏好、背景信息(见 DESIGN.md §10)。把它作为语义任务的**「透镜」**:
身份表用于本人识别,职责 / 重点用于相关性判断,时区 / 默认时间用于 Todo 自然语言解析。digest
飞书文档评论时,`context.md` 中明确的直属上司 / 汇报对象和用户点名“特别关注其观点”的人员是
P0 必看,使用者本人及明确的上级链路 / 主管方向负责人是 P1 高关注;这只提高抽取与提醒优先级,
不提高其观点本身的事实置信度。身份 / 职责是**用户提供的信息**;当前重点 / 主管方向等主观内容
呈现时标为「你的视角 / 用户陈述」,不硬化为客观事实。`context.md` 是真相源 —— 只读、绝不
擅自改写;用户要增删改走子命令 `context`。姓名 / 别名 / feishu_id 仍是“待补充”且本次任务
需要识别本人时,合并成一次简短询问,不在无关操作中反复打断。

**知识库检索回答引用(每次必做)**:凡用户可见回答中的事实来自 `knowledge/`、`raw_data/`、
`reports/`、`journal/` 或 `dashboard.md` 派生内容,必须读取并执行
[`references/citations.md`](references/citations.md):正文用 `[S1]` 等编号把具体结论绑定到证据,
末尾逐条给出原始文档 / 妙记录屏 / 会议 / 群聊窗口 / 网页 / 本地文件、原文时间或覆盖范围、
raw 的 `ingested` 收录时间及版本。不得只列节点 id / raw_id / 报告路径;缺字段必须明确写
“未记录”并降低置信度,不得用文件名或节点更新时间猜测。此规则覆盖 `search`、`brief`、
`dashboard`、日报 / 周报 / IM 报告的生成与回显,以及任何实际检索知识库的自然语言回答。

**Todo 状态检查(preflight 内部)**:不要另跑 `init/check`。没有到期 / 临期事项时 preflight
无输出；有则返回 `TODO_REMINDERS`，在当前回答开头提醒，真正展示后调用 `mark-reminded`
限频。检查不等于后台推送:只能保证每个实际调用 Byteworker preflight 的 session 执行，
不能保证未加载本 skill 的无关对话或无对话时主动提醒。

**长流程状态输出**:digest / 跑定期摄取 / 交互式报告补跑 / IM Inbox / 大输入摄取等可能耗时较久的多步操作,必须给用户阶段性状态,避免长时间沉默。规则:
- 开始长流程时先发一句说明本次会做哪几步,例如「我先拉取原文,再做幂等检查和节点写入」。
- 每完成一个关键阶段都回显一行短状态:已定位来源、已拉取原文、已完成幂等检查、正在实体消解、正在写入节点、正在生成报告、正在创建本地回滚点等。
- 单个阶段若超过约 30-60 秒仍未完成,发一条 heartbeat:说明仍在处理哪个阶段、目前已处理到什么数量/哪类来源;不要编造预计剩余时间。
- 状态输出只写元信息和数量,不要粘贴业务原文、聊天全文、敏感节点正文或大段候选 JSON。
- 若调用脚本,把脚本的关键 notice / warning / stats 转述给用户;无输出的安静脚本不必额外制造噪音。

**其他**

- **schema 真相源是 [`DESIGN.md`](DESIGN.md)** —— 节点类型、字段、命名规范、目录结构以它为准。每类节点的字段与 body 章节已编码进 `templates/node-<type>.md`,日常写节点照模板即可;命名规范、目录布局、数据不变量等**拿不准时再查 DESIGN.md**,不必每次通读。
- 知识库是一张实体图:7 类节点 —— `person` / `project` / `area` / `org` 是持续更新的**实体**,`event` / `decision` 是产生即定型的**记录**,`reading` 是读物 / 资料卡(外部 blog/论文/wiki,以及内部路线思考、方法论、调研、白皮书)—— 通过 frontmatter 的 `links` 互链。
- 节点骨架在 [`templates/`](templates/) 下 `node-<type>.md`。

## 安全约束(必须遵守)

- **逻辑与数据严格分离**:本 skill 仓库只含 agent 逻辑(可放 GitHub);知识库数据目录含**公司机密工作内容**。
- **绝不**把任何业务数据(节点 md、raw_data、provenance、journal、INDEX)写进本 skill 目录;数据一律写到知识库数据目录。
- 知识库数据目录**绝不**纳入本 skill 仓库的 git,**绝不** push 到任何 remote,不得外传。
- **不调用 lark-task 创建任务** —— 会议待办仅以 md 形式记录在 `event` 节点的"待办事项"章节内。

---

## digest — 摄取

**触发**:子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、Meego /
多维表格视图、风神看板、飞书知识库空间、外部文章或本地 md,说"存入知识库""消化这个"
"探索知识库""记一下"等。

若目标是**探索一整个飞书知识库空间、挑选页面或恢复批量 Wiki digest**，先且只读
`references/digest-wiki-space.md`；用户确认具体页面后，才按普通飞书文档加载标准 digest 细则。

完整主流程已拆到 `references/digest-core.md`。执行 digest 前必须先读它和
`references/digest-dependencies.md`(识别重要依赖、必要时向用户确认扩展范围)以及
`references/digest-transaction.md`(确定性 hash / 幂等 / 写入事务)、
`references/provenance.md`(主要来源、精确定位与 `[E]` 事实证据);再按来源类型加读对应细则:

- `feishu_doc` → `references/digest-doc.md`(正文 + 全部评论 / 回复;该文件继续路由
  `references/digest-comments.md`;正文含白板时再路由 `references/digest-whiteboard.md`)
- `feishu_chat` → `references/digest-chat.md`
- `meego` → `references/digest-meego.md`(第一版只读保存视图)
- `feishu_base` → `references/digest-base.md`(第一版只读明确 Base 视图)
- `feishu_wiki` 空间探索 → `references/digest-wiki-space.md`(只扫描树与选择页面；页面仍按
  `feishu_doc` digest)
- `aeolus` → `references/digest-aeolus.md`(一 sheet 一 KB profile、只读筛选重放与规范快照)
- `web` / 内部资料型文档 → `references/digest-reading.md`
- 会议簇(日历 / 投屏文档 / 妙记同属一场会) → `references/digest-meeting.md`
- 立场分析 / 思路视角沉淀 → `references/digest-analysis.md`
- 大型输入 → `references/digest-large.md`
- 不带来源的 digest / 跑定期摄取 → `references/digest-routine.md`

标准单来源 digest 写入必须由来源 adapter 先生成
`byteworker-source-bundle/v2`，再由 Agent生成只引用该 bundle 的
`digest-plan/v2` 与完整候选节点，通过
`bin/digest-txn.py preflight / validate / execute` 完成;Agent 实际调用使用
`bin/byteworker digest-txn ...` 机器协议。脚本只固化确定性执行,语义判断、冲突
裁决、实体取舍和节点正文仍由 Agent负责。候选节点的关键知识库事实必须逐条带 `[E1]` 等标记,
并在 manifest 中映射到 `raw_id + anchor_id`;主记录必须声明 `primary_source`,事务负责生成
`primary_source_url` 和 `## 证据`。不得为单篇业务资料在 skill 仓库生成硬编码写入脚本。
`digest-plan/v1` 仅作为已有调用方兼容入口；新代码不得继续在 plan 内复制 provider-specific
`source` 或 `provenance.anchors`。
Meego/Base/Aeolus 完整 capture 使用 `source capture --bundle-out` 同步生成 Bundle；
已保存 Profile 的群聊 capture 直接输出 Bundle；飞书文档、妙记、Web 和本地文件由宿主能力
抓取原始 artifact 后，调用 `source bundle --source-type ... --request ... --out ...`。
会议簇保持上层复合编排：妙记和每份投屏文档先各自产生 Bundle，不把整场会议伪装成单一
provider capture。
两个以上来源需要原子落库或共同更新一个节点时，使用只引用各自 Bundle 的
`digest-batch-plan/v2`；`digest-batch-plan/v1` 仅兼容旧调用。batch 仍只持一个短时写锁并
产生一个 commit。所有业务 component、manifest 和候选文件都必须位于系统临时目录或知识库
目录，事务 CLI 会拒绝 skill 仓库内路径。
新建或更新 `person` 时必须用 `bin/byteworker run bin/resolve-users.sh --format json` 同次取得身份与通讯录画像：
按 `feishu_id` 消解，记录 `directory_verified_at`，并在可见时同步企业邮箱和当前部门路径；
部门变化保留历史，空结果不清除旧值。完整规则见 `references/digest-core.md`。
其它写入遵守 `references/write-rules.md`;失败处理见 `references/error-handling.md`。

## search / update / brief / dashboard / context

这些子命令的完整流程已拆到 `references/commands.md`。执行前按需读取对应小节:

- `search`:查询知识库。先通过机器协议调用 `bin/kb-query.py search` 做有覆盖回执的字面/全文召回
  和一跳图扩展,再由 Agent 语义补召回；若问题指向 Meego / Base / 风神的具体需求、记录 ID 或标题，
  或知识节点只含宏观摘要而不足以回答具体记录，必须通过机器协议调用
  `bin/byteworker kb-query source-record`
  从最新结构化 raw 快照按稳定 ID / 模糊标题做有限召回，禁止让 Agent 直接扫描大 raw；
  节点有 `[E]` 时调用 `bin/kb-query.py evidence` 精确解析出处。按
  `references/citations.md` 给每条知识库事实附原始出处、收录时间与置信度。
- `update`:定位目标节点,必要时先 digest 新输入为 raw,再做冲突检测与合并。
- `brief`:读取日程,按会议主题/参会人查知识库并生成会前上下文。
- `dashboard`:刷新或维护 `dashboard.md`。派生视图可重算,固定/手动项保留。
- `context`:仅在用户明确要求增删改全局工作上下文时维护 `context.md`;其它流程只读,绝不擅改。

写操作遵守 `references/write-rules.md`。

## todo — 自然语言待办与提醒

**触发**:子命令 `todo`;或自然语言 —— “记个待办”“提醒我”“明天 / 后天 / 下周六要做 X”
“刚才那个做完了”“把 X 延期到下周二”“取消刚刚的提醒”“我还有什么没做”。

完整流程见 `references/todo.md`。用户侧以自然语言为主;todo id 只在 agent 内部调用脚本时使用,
不要要求用户记忆或输入。时间由 agent 提取任务标题 / 提醒 / 截止语义后通过机器协议交给
`bin/todo.py`
结合 `context.md` 解析,写入后回显绝对时间供用户纠正。

## 自动日报 / 周报

**触发**:宿主 harness 的本地定时任务;或用户明确要求设置、修改、暂停自动报告,以及补生成 /
重跑指定日期或 ISO 周。没有 `daily` / `weekly` 用户子命令。

设置、迁移和无人值守边界见 `references/report-scheduling.md`;报告内容细则见
`references/periodic-report.md`。定时任务 prompt 使用
`templates/report-automation-daily.md` / `templates/report-automation-weekly.md`。
离线、休眠或瞬时网络失败的补偿任务使用
`templates/report-automation-recovery.md`；它周期性调用 `report-automation check`，每次最多
补跑一个没有成功回执的周期。

- 自动日报每次运行都先执行**完整 routine digest**,不受 `.last-routine-digest` 七天提醒阈值
  限制;随后生成当天 00:00 到当前时刻的 `reports/daily/<YYYY-MM-DD>.md`。
- 自动周报同样先执行完整 routine digest,随后生成上一完整 ISO 周的
  `reports/weekly/<YYYY>-W<WW>.md`。
- 只重放已登记且启用的 routine 来源;自动报告不授权新增来源、扩大摄取范围、发起 OAuth、
  切换身份、发送消息或 push。
- 开始前通过 `report-automation lease` 获取跨报告租约,结束后按真实结果记录 success / failed。
- `lease` 在启动时记录 `last_attempt`；`complete` 同时维护 `last_run`，成功时另更新
  `last_success`。补偿检查只以对应 period 的 `last_success` 判断完成，失败不会抹掉上次成功。
- 用户明确要求「分析最近一天 IM / 聊天重点 / 日报包含 IM」时才加读
  `references/im-inbox-summary.md`;默认自动报告不全量扫描 IM。

## inbox — IM Inbox 摘要

**触发**:子命令 `inbox`;或自然语言 —— "分析今天飞书 IM 重要消息""昨天聊天里有什么要关注""最近一天 IM 重点"。

执行细则见 `references/im-inbox-summary.md`。默认扫描今天;用户说"昨天"取上一自然日;用户说"最近一天 / 过去 24 小时"取滚动 24 小时;用户给 `YYYY-MM-DD` 取该日 00:00-23:59:59。输出写到知识库数据目录 `reports/im/<YYYY-MM-DD>.md` 或非自然日窗口文件;不跑定期摄取,不生成日报 / 周报,不把全量 IM 原文写入 `raw_data/`。

## doctor — 兼容性检查与修复

**触发**:子命令 `doctor`;或自然语言 —— “检查知识库”“升级 skill 后数据兼容吗”“扫描并修复
知识库”“schema 有没有漂移”。

完整流程见 `references/doctor.md`。用户主动调用时默认通过机器协议执行只读
`bin/byteworker doctor scan`;
只有用户明确要求修复才执行 `fix`。例外是代码实际自动更新后的 post-update doctor:可直接处理
finding 明确声明的 `auto_fix`。自动修复只覆盖可确定重建的 INDEX 与 links;缺失业务字段、
证据链、悬空 id、真相源损坏只列问题和建议,不得猜写。scan 同时检查 `sources/` Profile、
定期来源的 Profile 覆盖、raw/Profile identity 和新事务持久化契约；缺失 Profile 只给分级
finding，绝不由 doctor 自动迁移或拼接 capture policy。

## help — 帮助

**触发**:子命令 `help`;或自然语言 —— "帮助""byteworker 怎么用""这个 skill 能做什么""用法"。

不读写知识库 —— 读 skill 目录的 `references/help.md`,把其全部内容**原样**输出给用户(放进代码块呈现)。

---

## 写入规范 / 重建与恢复

写入、重建和修复规则已拆分:

- **写入前必读**:`references/write-rules.md` —— 原子写入、双向 links、auto-link、INDEX、journal、本地 git 回滚点、时间格式、时间倒序。
- **兼容性诊断**:`references/doctor.md` —— 先只读扫描当前 schema/profile,再按用户授权做确定性修复。
- **维护 / 恢复按需读**:`references/maintenance.md` —— 通过机器协议 `index rebuild` 重建
  INDEX（shell wrapper 仅供人工排障）、运行 `bin/byteworker run bin/repair-links.sh --autolink` 修复双链 /
  正文提及连边、灾难恢复。

核心不变量:知识库数据目录是唯一业务数据位置;`raw_data/` + `provenance/` + `knowledge/` + `reports/` + `todo.md` + `context.md` + `dashboard.md` 手动项是真相源,`INDEX.md` 和 dashboard 派生段可重建。

系统的信息处理流程、代码分层、模块职责与依赖方向以
[`ARCHITECTURE.md`](ARCHITECTURE.md) 为准。coding agent 修改代码或主流程前必须先读对应章节；
若模块、依赖、跨层契约、成功/失败路径发生变化，必须在同一变更中同步该文档和架构契约测试。
持久化 schema 仍以 [`DESIGN.md`](DESIGN.md) 为准，Agent 行为仍以本文件及 `references/` 为准。

## 错误处理

错误处理表已拆到 `references/error-handling.md`。摄取/写入失败时按该文件处理:无权限或资源失效中止、不写 raw;会议无纪要提示稍后;网络超时重试一次;写入中断依靠 temp-then-move 保证不留下半成品。

> LLM digest 有丢事实/幻觉风险:`raw_data` 逐字保留 + 节点 `sources` 溯源。
> 任何知识库回答都必须把结论绑定到原始出处并展示收录时间,使用户能回原文核对并判断是否过期。
> digest 时不确定的内容宁可标注存疑,不臆造。
