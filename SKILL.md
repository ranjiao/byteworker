---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、会议妙记、会议、群聊、外部 blog/论文/wiki、本地 md 摄取(digest)并消化成结构化实体图笔记(人员/项目/主题领域/组织/事件/决策/读物),支持对话式查询(search)、更新(update)、会前简报(brief)、工作看板(dashboard)、日报(daily)、周报(weekly)、对话式维护全局工作上下文(context)。当用户要把飞书文档/妙记 URL/会议/群聊/外部文章/本地 md 存入知识库、消化某份资料或某个群的讨论、查询"关于X我知道什么"或"我们关于Y定过什么"、查项目进度、更新某条知识、要会前简报/日报/周报、查看工作看板、或想了解这个 skill 怎么用(help)时使用;支持 /byteworker digest/search/update/brief/dashboard/daily/weekly/context/help 子命令。
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
| `daily` | 日报 | 自动跑定期摄取,总结当天重要事项并生成日报 | `/byteworker daily` |
| `weekly` | 周报 | 自动跑定期摄取,总结本周重要事项并生成周报 | `/byteworker weekly` |
| `context` | 全局上下文 | 对话式增删改你的工作上下文 | `/byteworker context 我的当前重点改成X` |
| `help` | 帮助 | 用法说明 | `/byteworker help` |

带子命令时按对应能力执行;未带子命令的自然语言输入,按各能力的「触发」判断。

## 操作前必读

**自动更新(最先做)**:每次使用本 skill,先静默运行 `bin/update-check.sh` —— 它每周最多实际检查一次,从 GitHub fast-forward 拉取最新 skill 内容。脚本**有输出**就把那一行转告用户,**无输出**则静默继续(更新于下次使用生效)。

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
- 若该数据目录不存在或为空:按 DESIGN.md §1.B 初始化 —— 创建 `knowledge/` 的 7 个子目录、`raw_data/`、`journal/`、`reports/daily/`、`reports/weekly/`、空 `INDEX.md`、`context.md`(**整份复制** skill 目录的 `templates/context.md`,统一格式),并对该数据目录执行 `git init`(**仅本地、永不配 remote**,作误删/错改的回滚网)。
- **下文所有 `knowledge/`、`raw_data/`、`journal/`、`reports/`、`INDEX.md`、`dashboard.md`、`context.md` 路径,一律指知识库数据目录下的对应路径;`templates/` 与 `DESIGN.md` 在本 skill 目录下。**

**定期摄取到期提醒**:本次操作若会读 `INDEX.md`,顺带看「定期摄取清单」—— 若清单非空、且数据目录的 `.last-routine-digest`(记上次「定期摄取」运行日期;文件不存在 = 从未运行)距今 ≥7 天 → 用一句话提醒用户「定期摄取清单有 N 项可能该查更新了,需要就说『跑定期摄取』」。**只提醒,不打断当前请求、不自动跑。**

**全局上下文(每次必读)**:读知识库数据目录下的 `context.md` —— 使用者主动维护的全局工作上下文(当前重点 / 主管方向 / 约束 / 背景,见 DESIGN.md §10)。把它作为本次 digest / search / brief / dashboard 的**「透镜」**:digest 时影响怎么解读、什么值得消化;search / brief 时在客观答案旁带出使用者视角与主管方向,并在**客观信息与某条陈述意图冲突时主动提示**。`context.md` 的内容呈现给用户时一律标为「你的视角 / 主管方向」,**非事实**;它是真相源 —— **本流程(操作前必读 / digest / search 等)中只读、绝不改写**;用户要增删改它走子命令 `context`(对话式 agent 用户由 agent 代维护,见「context」一节)。`context.md` 不存在 → 用 skill 目录的 `templates/context.md` 初始化一个(整份复制、静默;统一模板避免格式漂移),本次视为空继续。

**其他**

- **schema 真相源是 [`DESIGN.md`](DESIGN.md)** —— 节点类型、字段、命名规范、目录结构以它为准。每类节点的字段与 body 章节已编码进 `templates/node-<type>.md`,日常写节点照模板即可;命名规范、目录布局、数据不变量等**拿不准时再查 DESIGN.md**,不必每次通读。
- 知识库是一张实体图:7 类节点 —— `person` / `project` / `area` / `org` 是持续更新的**实体**,`event` / `decision` 是产生即定型的**记录**,`reading` 是读物 / 资料卡(外部 blog/论文/wiki,以及内部路线思考、方法论、调研、白皮书)—— 通过 frontmatter 的 `links` 互链。
- 节点骨架在 [`templates/`](templates/) 下 `node-<type>.md`。

## 安全约束(必须遵守)

- **逻辑与数据严格分离**:本 skill 仓库只含 agent 逻辑(可放 GitHub);知识库数据目录含**公司机密工作内容**。
- **绝不**把任何业务数据(节点 md、raw_data、journal、INDEX)写进本 skill 目录;数据一律写到知识库数据目录。
- 知识库数据目录**绝不**纳入本 skill 仓库的 git,**绝不** push 到任何 remote,不得外传。
- **不调用 lark-task 创建任务** —— 会议待办仅以 md 形式记录在 `event` 节点的"待办事项"章节内。

---

## digest — 摄取

**触发**:子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、外部文章或本地 md,说"存入知识库""消化这个""记一下"等。

完整主流程已拆到 `references/digest-core.md`。执行 digest 前必须先读它;再按来源类型加读对应细则:

- `feishu_doc` → `references/digest-doc.md`
- `feishu_chat` → `references/digest-chat.md`
- `web` / 内部资料型文档 → `references/digest-reading.md`
- 会议簇(日历 / 投屏文档 / 妙记同属一场会) → `references/digest-meeting.md`
- 立场分析 / 思路视角沉淀 → `references/digest-analysis.md`
- 大型输入 → `references/digest-large.md`
- 不带来源的 digest / 跑定期摄取 → `references/digest-routine.md`

写入遵守 `references/write-rules.md`;失败处理见 `references/error-handling.md`。

## search / update / brief / dashboard / context

这些子命令的完整流程已拆到 `references/commands.md`。执行前按需读取对应小节:

- `search`:查询知识库。必须双路召回(语义扫 INDEX + 全文 grep),并沿 `links` 做图遍历;答案必须附 sources 与置信度。
- `update`:定位目标节点,必要时先 digest 新输入为 raw,再做冲突检测与合并。
- `brief`:读取日程,按会议主题/参会人查知识库并生成会前上下文。
- `dashboard`:刷新或维护 `dashboard.md`。派生视图可重算,固定/手动项保留。
- `context`:仅在用户明确要求增删改全局工作上下文时维护 `context.md`;其它流程只读,绝不擅改。

写操作遵守 `references/write-rules.md`。

## daily — 日报

**触发**:子命令 `daily`;或自然语言 —— "生成今天日报""今天工作总结""更新日报"。

执行细则见 `references/periodic-report.md`。日报文件写到知识库数据目录 `reports/daily/<YYYY-MM-DD>.md`;先自动跑定期摄取,再按当天材料生成工作总结快照。

## weekly — 周报

**触发**:子命令 `weekly`;或自然语言 —— "生成本周周报""更新周报""这周工作总结"。

执行细则见 `references/periodic-report.md`。周报文件写到知识库数据目录 `reports/weekly/<YYYY>-W<WW>.md`;默认当前 ISO 周,用户说"上周"则取上一完整 ISO 周。

## help — 帮助

**触发**:子命令 `help`;或自然语言 —— "帮助""byteworker 怎么用""这个 skill 能做什么""用法"。

不读写知识库 —— 读 skill 目录的 `references/help.md`,把其全部内容**原样**输出给用户(放进代码块呈现)。

---

## 写入规范 / 重建与恢复

写入、重建和修复规则已拆分:

- **写入前必读**:`references/write-rules.md` —— 原子写入、双向 links、auto-link、INDEX、journal、本地 git 回滚点、时间格式、时间倒序。
- **维护 / 恢复按需读**:`references/maintenance.md` —— 运行 `bin/rebuild-index.sh` 重建 INDEX、运行 `bin/repair-links.sh --autolink` 修复双链 / 正文提及连边、灾难恢复。

核心不变量:知识库数据目录是唯一业务数据位置;`raw_data/` + `knowledge/` + `reports/` + `dashboard.md` 手动项是真相源,`INDEX.md` 和 dashboard 派生段可重建。

## 错误处理

错误处理表已拆到 `references/error-handling.md`。摄取/写入失败时按该文件处理:无权限或资源失效中止、不写 raw;会议无纪要提示稍后;网络超时重试一次;写入中断依靠 temp-then-move 保证不留下半成品。

> LLM digest 有丢事实/幻觉风险:`raw_data` 逐字保留 + 节点 `sources` 溯源,
> 任何答案都可回原文核对。digest 时不确定的内容宁可标注存疑,不臆造。
