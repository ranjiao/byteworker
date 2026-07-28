---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、会议妙记、会议、群聊、外部 blog/论文/wiki、本地 md 摄取(digest)并消化成结构化实体图笔记(人员/项目/主题领域/组织/事件/决策/读物),支持对话式查询(search)、更新(update)、会前简报(brief)、工作看板(dashboard)、自然语言待办与提醒(todo)、日报(daily)、周报(weekly)、IM Inbox 摘要(inbox)、对话式维护全局工作上下文(context),以及扫描/修复知识库与当前 skill/schema 不兼容问题(doctor)。当用户要把资料存入知识库、查询或更新工作知识、要会前简报/日报/周报、分析飞书 IM、查看工作看板、检查/修复知识库、升级 skill 后排查数据兼容性,或说“记个待办”“明天/后天/下周六提醒我”“刚才那个做完了”“延期/取消提醒”“看看还有什么没做”时使用;支持 /byteworker digest/search/update/brief/dashboard/todo/daily/weekly/inbox/context/doctor/help 子命令,但 todo 日常以自然语言为主。
---

# byteworker 个人知识库

把日常飞书工作信息消化成结构化的**实体图知识库**,供对话式查询与更新。

> **用法**:`/byteworker 子命令 [参数]`(如 `/byteworker digest 飞书URL`),或直接自然语言。
> 不清楚怎么用就 `/byteworker help`。

## 子命令

| 子命令 | 中文 | 作用 | 示例 |
|--------|------|------|------|
| `digest` | 摄取 | 把飞书文档/妙记/会议/群聊/md 消化入库 | `/byteworker digest <飞书URL>` |
| `search` | 查询 | 问知识库 | `/byteworker search 我们关于X定过什么` |
| `update` | 更新 | 某条知识有新进展 | `/byteworker update Y项目有进展` |
| `brief` | 会前简报 | 开会前拉相关上下文 | `/byteworker brief` |
| `dashboard` | 工作看板 | 看当下该关注什么 | `/byteworker dashboard` |
| `todo` | 待办提醒 | 用自然语言增加、完成、延期、取消或查看待办 | `明天下午三点提醒我提交周报` |
| `daily` | 日报 | 自动跑定期摄取,总结当天重要事项并生成日报 | `/byteworker daily` |
| `weekly` | 周报 | 自动跑定期摄取,总结本周重要事项并生成周报 | `/byteworker weekly` |
| `inbox` | IM摘要 | 扫描飞书 IM 高信号消息并生成摘要 | `/byteworker inbox 昨天` |
| `context` | 全局上下文 | 对话式增删改你的工作上下文 | `/byteworker context 我的当前重点改成X` |
| `doctor` | 兼容诊断 | 扫描知识库与当前 schema 的不匹配并做确定性修复 | `/byteworker doctor` |
| `help` | 帮助 | 用法说明 | `/byteworker help` |

带子命令时按对应能力执行;未带子命令的自然语言输入,按各能力的「触发」判断。

## 操作前必读

**自动更新(最先做)**:每次使用本 skill,先静默运行 `bin/update-check.sh` —— 它每周最多实际检查一次,从 GitHub fast-forward 拉取最新 skill 内容。只有代码确实更新后才自动运行 post-update doctor:按 `auto_fix` 白名单修复确定性低成本问题、复扫并创建知识库本地回滚提交。脚本**有输出**就把那一行转告用户,**无输出**则静默继续(代码更新于下次使用生效)。

- doctor 结果含无法自动处理的 `error`、修复失败或相关文件正被编辑 → 告知“严重问题”,请用户决定是否立即检查,当前业务请求可在不依赖损坏数据时继续。
- 只剩 `warning` / `info` → 用脚本给出的单行数量摘要告知,让用户选择忽略或立即处理;不要展开全量 finding。
- 自动更新没有发生 → 不运行 post-update doctor,避免每次调用都做全库扫描。

- **无需 GitHub 账号/SSH key**:仓库是 public repo,脚本会自动使用 HTTPS 拉取;若你当前 origin 是 SSH(`git@github.com`) 但环境无 SSH key,脚本会 fallback 到 HTTPS 临时拉取,**默认不改写 origin**。确需让脚本补 / 改 remote 时,手动设置 `BYTEWORKER_AUTO_UPDATE_MUTATE_ORIGIN=1` 后再运行。
- **主动触发**:用户说"更新 skill""检查更新""byteworker 有新版吗" → 调用 `bin/update-check.sh --force`(跳过 7 天周期,立即检查)。
- **失败提示**:网络不通/本地有改动导致无法 fast-forward 时,脚本会输出一行提示(不再完全静默) —— 把提示转告用户即可。

**本 skill 只含 agent 逻辑。业务数据(知识库内容)存在独立的「知识库数据目录」,不在本 skill 仓库内,也不进本仓库的 git。**

- 读本 skill 目录下的 `.kbconfig`(已 gitignore),其中一行是知识库数据目录的绝对路径。
- 若 `.kbconfig` 不存在(**首次使用**):
  - **先问用户要不要走「上手引导」** —— 一句话:「看来是第一次用 byteworker,要不要花 1-2 分钟过一遍上手流程(建库 → 摄取一篇文档 → 查询一次)?回复『跳过』可直接开始。」
    - **同意** → 读本 skill 目录下的 [`TUTORIAL.md`](TUTORIAL.md),按其剧本带用户走;引导**内含建库**那一步,走完即转入正常使用,不必再走下面的「常规首次设置」。
    - **跳过** → 走「常规首次设置」。
  - **常规首次设置**:**主动询问用户**知识库数据目录放在哪里 —— 让用户给一个父目录,目录名默认 `byteworker_kb`(用户可改);拼出绝对路径后写入 `.kbconfig`。
- 用户之后想再看引导(说「跑一下上手引导」「重看教程」等)→ 读 `TUTORIAL.md` 重走一遍(`.kbconfig` 已存在则跳过其中的建库步骤)。
- 若该数据目录不存在或为空:按 DESIGN.md §1.B 初始化 —— 创建 `knowledge/` 的 7 个子目录、`raw_data/`、`provenance/`、`journal/`、`reports/daily/`、`reports/weekly/`、`reports/im/`、空 `INDEX.md`,并把 skill 目录的 `templates/context.md` / `templates/todo.md` 整份复制为数据目录的 `context.md` / `todo.md`;再对该数据目录执行 `git init`(**仅本地、永不配 remote**,作误删/错改的回滚网)。
- **下文所有 `knowledge/`、`raw_data/`、`provenance/`、`journal/`、`reports/`、`INDEX.md`、`dashboard.md`、`context.md`、`todo.md` 路径,一律指知识库数据目录下的对应路径;`templates/` 与 `DESIGN.md` 在本 skill 目录下。**

**定期摄取到期提醒**:本次操作若会读 `INDEX.md`,顺带看「定期摄取清单」—— 若清单非空、且数据目录的 `.last-routine-digest`(记上次「定期摄取」运行日期;文件不存在 = 从未运行)距今 ≥7 天 → 用一句话提醒用户「定期摄取清单有 N 项可能该查更新了,需要就说『跑定期摄取』」。**只提醒,不打断当前请求、不自动跑。**

**全局上下文(每次必读)**:读知识库数据目录下的 `context.md` —— 固定包含使用者身份、职责范围、当前重点、主管方向、当前约束、交互与提醒偏好、背景信息(见 DESIGN.md §10)。把它作为本次 digest / search / brief / dashboard / todo 的**「透镜」**:身份表用于本人识别,职责 / 重点用于相关性判断,时区 / 默认时间用于 Todo 自然语言解析。digest 飞书文档评论时,`context.md` 中明确的直属上司 / 汇报对象和用户点名“特别关注其观点”的人员是 P0 必看,使用者本人及明确的上级链路 / 主管方向负责人是 P1 高关注;这只提高抽取与提醒优先级,不提高其观点本身的事实置信度。身份 / 职责是**用户提供的信息**;当前重点 / 主管方向等主观内容呈现时标为「你的视角 / 用户陈述」,不硬化为客观事实。`context.md` 是真相源 —— **本流程(操作前必读 / digest / search 等)中只读、绝不擅自改写**;用户要增删改走子命令 `context`。文件不存在 → 整份复制 `templates/context.md` 初始化;姓名 / 别名 / feishu_id 仍是“待补充”且本次任务需要识别本人时,合并成一次简短询问,不在无关操作中反复打断。

**知识库检索回答引用(每次必做)**:凡用户可见回答中的事实来自 `knowledge/`、`raw_data/`、
`reports/`、`journal/` 或 `dashboard.md` 派生内容,必须读取并执行
[`references/citations.md`](references/citations.md):正文用 `[S1]` 等编号把具体结论绑定到证据,
末尾逐条给出原始文档 / 妙记录屏 / 会议 / 群聊窗口 / 网页 / 本地文件、原文时间或覆盖范围、
raw 的 `ingested` 收录时间及版本。不得只列节点 id / raw_id / 报告路径;缺字段必须明确写
“未记录”并降低置信度,不得用文件名或节点更新时间猜测。此规则覆盖 `search`、`brief`、
`dashboard`、日报 / 周报 / IM 报告的生成与回显,以及任何实际检索知识库的自然语言回答。

**Todo 状态检查(每次必做)**:完成上面的 `context.md` 读取后,按 `references/todo.md` 运行 `python3 bin/todo.py <知识库目录> init --template templates/todo.md` 与 `check`。没有到期 / 临期事项则静默;有则在当前回答开头提醒,真正展示后调用 `mark-reminded` 限频。检查不等于后台推送:只能保证每次 byteworker 被宿主加载并运行时执行,不能保证未加载本 skill 的无关对话或无对话时主动提醒。

**长流程状态输出**:digest / 跑定期摄取 / daily / weekly / IM Inbox / 大输入摄取等可能耗时较久的多步操作,必须给用户阶段性状态,避免长时间沉默。规则:
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

**触发**:子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、外部文章或本地 md,说"存入知识库""消化这个""记一下"等。

完整主流程已拆到 `references/digest-core.md`。执行 digest 前必须先读它和
`references/digest-dependencies.md`(识别重要依赖、必要时向用户确认扩展范围)以及
`references/digest-transaction.md`(确定性 hash / 幂等 / 写入事务)、
`references/provenance.md`(主要来源、精确定位与 `[E]` 事实证据);再按来源类型加读对应细则:

- `feishu_doc` → `references/digest-doc.md`(正文 + 全部评论 / 回复;该文件继续路由
  `references/digest-comments.md`;正文含白板时再路由 `references/digest-whiteboard.md`)
- `feishu_chat` → `references/digest-chat.md`
- `web` / 内部资料型文档 → `references/digest-reading.md`
- 会议簇(日历 / 投屏文档 / 妙记同属一场会) → `references/digest-meeting.md`
- 立场分析 / 思路视角沉淀 → `references/digest-analysis.md`
- 大型输入 → `references/digest-large.md`
- 不带来源的 digest / 跑定期摄取 → `references/digest-routine.md`

标准 digest 写入必须由 Agent生成临时 manifest 与完整候选节点,再通过
`bin/digest-txn.py preflight / validate / execute` 完成;脚本只固化确定性执行,语义判断、冲突
裁决、实体取舍和节点正文仍由 Agent负责。候选节点的关键知识库事实必须逐条带 `[E1]` 等标记,
并在 manifest 中映射到 `raw_id + anchor_id`;主记录必须声明 `primary_source`,事务负责生成
`primary_source_url` 和 `## 证据`。不得为单篇业务资料在 skill 仓库生成硬编码写入脚本。
两个以上来源需要原子落库或共同更新一个节点时，使用 `digest-batch-plan/v1`；它仍只持一个短时
写锁并产生一个 commit。所有业务 component、manifest 和候选文件都必须位于系统临时目录或
知识库目录，事务 CLI 会拒绝 skill 仓库内路径。
其它写入遵守 `references/write-rules.md`;失败处理见 `references/error-handling.md`。

## search / update / brief / dashboard / context

这些子命令的完整流程已拆到 `references/commands.md`。执行前按需读取对应小节:

- `search`:查询知识库。先用 `bin/kb-query.py search` 做有覆盖回执的字面/全文召回和一跳图扩展,
  再由 Agent 语义补召回；节点有 `[E]` 时用 `bin/kb-query.py evidence` 精确解析出处。按
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
不要要求用户记忆或输入。时间由 agent 提取任务标题 / 提醒 / 截止语义后交给 `bin/todo.py`
结合 `context.md` 解析,写入后回显绝对时间供用户纠正。

## daily — 日报

**触发**:子命令 `daily`;或自然语言 —— "生成今天日报""今天工作总结""更新日报"。

执行细则见 `references/periodic-report.md`。日报文件写到知识库数据目录 `reports/daily/<YYYY-MM-DD>.md`;先自动跑定期摄取,再按当天材料生成工作总结快照。

若用户明确要求「分析最近一天 IM / 聊天重点 / 日报包含 IM」,加读 `references/im-inbox-summary.md`:先用本地规则、预算和 thread 聚类降噪,再只对高信号候选做 LLM 精判;默认把最终摘要保存到 `reports/im/`,不全量归档 IM 原文。

## weekly — 周报

**触发**:子命令 `weekly`;或自然语言 —— "生成本周周报""更新周报""这周工作总结"。

执行细则见 `references/periodic-report.md`。周报文件写到知识库数据目录 `reports/weekly/<YYYY>-W<WW>.md`;默认当前 ISO 周,用户说"上周"则取上一完整 ISO 周。

## inbox — IM Inbox 摘要

**触发**:子命令 `inbox`;或自然语言 —— "分析今天飞书 IM 重要消息""昨天聊天里有什么要关注""最近一天 IM 重点"。

执行细则见 `references/im-inbox-summary.md`。默认扫描今天;用户说"昨天"取上一自然日;用户说"最近一天 / 过去 24 小时"取滚动 24 小时;用户给 `YYYY-MM-DD` 取该日 00:00-23:59:59。输出写到知识库数据目录 `reports/im/<YYYY-MM-DD>.md` 或非自然日窗口文件;不跑定期摄取,不生成 daily / weekly,不把全量 IM 原文写入 `raw_data/`。

## doctor — 兼容性检查与修复

**触发**:子命令 `doctor`;或自然语言 —— “检查知识库”“升级 skill 后数据兼容吗”“扫描并修复
知识库”“schema 有没有漂移”。

完整流程见 `references/doctor.md`。用户主动调用时默认执行只读 `python3 bin/doctor.py scan`;
只有用户明确要求修复才执行 `fix`。例外是代码实际自动更新后的 post-update doctor:可直接处理
finding 明确声明的 `auto_fix`。自动修复只覆盖可确定重建的 INDEX 与 links;缺失业务字段、
证据链、悬空 id、真相源损坏只列问题和建议,不得猜写。

## help — 帮助

**触发**:子命令 `help`;或自然语言 —— "帮助""byteworker 怎么用""这个 skill 能做什么""用法"。

不读写知识库 —— 读 skill 目录的 `references/help.md`,把其全部内容**原样**输出给用户(放进代码块呈现)。

---

## 写入规范 / 重建与恢复

写入、重建和修复规则已拆分:

- **写入前必读**:`references/write-rules.md` —— 原子写入、双向 links、auto-link、INDEX、journal、本地 git 回滚点、时间格式、时间倒序。
- **兼容性诊断**:`references/doctor.md` —— 先只读扫描当前 schema/profile,再按用户授权做确定性修复。
- **维护 / 恢复按需读**:`references/maintenance.md` —— 运行 `bin/rebuild-index.sh` 重建 INDEX、运行 `bin/repair-links.sh --autolink` 修复双链 / 正文提及连边、灾难恢复。

核心不变量:知识库数据目录是唯一业务数据位置;`raw_data/` + `provenance/` + `knowledge/` + `reports/` + `todo.md` + `context.md` + `dashboard.md` 手动项是真相源,`INDEX.md` 和 dashboard 派生段可重建。

## 错误处理

错误处理表已拆到 `references/error-handling.md`。摄取/写入失败时按该文件处理:无权限或资源失效中止、不写 raw;会议无纪要提示稍后;网络超时重试一次;写入中断依靠 temp-then-move 保证不留下半成品。

> LLM digest 有丢事实/幻觉风险:`raw_data` 逐字保留 + 节点 `sources` 溯源。
> 任何知识库回答都必须把结论绑定到原始出处并展示收录时间,使用户能回原文核对并判断是否过期。
> digest 时不确定的内容宁可标注存疑,不臆造。
