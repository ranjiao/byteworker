---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、会议妙记、会议、群聊、外部 blog/论文/wiki、本地 md 摄取(digest)并消化成结构化实体图笔记(人员/项目/主题领域/组织/事件/决策/读物),支持对话式查询(search)、更新(update)、会前简报(brief)、工作看板(dashboard)、对话式维护全局工作上下文(context)。当用户要把飞书文档/妙记 URL/会议/群聊/外部文章/本地 md 存入知识库、消化某份资料或某个群的讨论、查询"关于X我知道什么"或"我们关于Y定过什么"、查项目进度、更新某条知识、要会前简报、查看工作看板、或想了解这个 skill 怎么用(help)时使用;支持 /byteworker <digest|search|update|brief|dashboard|context|help> 子命令。
---

# byteworker 个人知识库

把日常飞书工作信息消化成结构化的**实体图知识库**,供对话式查询与更新。

> **用法**:`/byteworker <子命令> [参数]`(如 `/byteworker digest <飞书URL>`),或直接自然语言。
> 不清楚怎么用就 `/byteworker help`。

## 子命令

| 子命令 | 中文 | 作用 | 示例 |
|--------|------|------|------|
| `digest` | 摄取 | 把飞书文档/妙记/会议/群聊/md 消化入库 | `/byteworker digest <飞书URL>` |
| `search` | 查询 | 问知识库 | `/byteworker search 我们关于X定过什么` |
| `update` | 更新 | 某条知识有新进展 | `/byteworker update Y项目有进展` |
| `brief` | 会前简报 | 开会前拉相关上下文 | `/byteworker brief` |
| `dashboard` | 工作看板 | 看当下该关注什么 | `/byteworker dashboard` |
| `context` | 全局上下文 | 对话式增删改你的工作上下文 | `/byteworker context 我的当前重点改成X` |
| `help` | 帮助 | 用法说明 | `/byteworker help` |

带子命令时按对应能力执行;未带子命令的自然语言输入,按各能力的「触发」判断。

## 操作前必读

**自动更新(最先做)**:每次使用本 skill,先静默运行 `bin/update-check.sh` —— 它每周最多实际检查一次,从 GitHub fast-forward 拉取最新 skill 内容。脚本**有输出**就把那一行转告用户,**无输出**则静默继续(更新于下次使用生效)。

**本 skill 只含 agent 逻辑。业务数据(知识库内容)存在独立的「知识库数据目录」,不在本 skill 仓库内,也不进本仓库的 git。**

- 读本 skill 目录下的 `.kbconfig`(已 gitignore),其中一行是知识库数据目录的绝对路径。
- 若 `.kbconfig` 不存在(**首次使用**):
  - **先问用户要不要走「上手引导」** —— 一句话:「看来是第一次用 byteworker,要不要花 1-2 分钟过一遍上手流程(建库 → 摄取一篇文档 → 查询一次)?回复『跳过』可直接开始。」
    - **同意** → 读本 skill 目录下的 [`TUTORIAL.md`](TUTORIAL.md),按其剧本带用户走;引导**内含建库**那一步,走完即转入正常使用,不必再走下面的「常规首次设置」。
    - **跳过** → 走「常规首次设置」。
  - **常规首次设置**:**主动询问用户**知识库数据目录放在哪里 —— 让用户给一个父目录,目录名默认 `byteworker_kb`(用户可改);拼出绝对路径后写入 `.kbconfig`。
- 用户之后想再看引导(说「跑一下上手引导」「重看教程」等)→ 读 `TUTORIAL.md` 重走一遍(`.kbconfig` 已存在则跳过其中的建库步骤)。
- 若该数据目录不存在或为空:按 DESIGN.md §1.B 初始化 —— 创建 `knowledge/` 的 7 个子目录、`raw_data/`、`journal/`、空 `INDEX.md`、`context.md`(**整份复制** skill 目录的 `templates/context.md`,统一格式),并对该数据目录执行 `git init`(**仅本地、永不配 remote**,作误删/错改的回滚网)。
- **下文所有 `knowledge/`、`raw_data/`、`journal/`、`INDEX.md`、`dashboard.md`、`context.md` 路径,一律指知识库数据目录下的对应路径;`templates/` 与 `DESIGN.md` 在本 skill 目录下。**

**定期摄取到期提醒**:本次操作若会读 `INDEX.md`,顺带看「定期摄取清单」—— 若清单非空、且 journal 显示距上次「定期摄取」运行 ≥7 天(或从未运行)→ 用一句话提醒用户「定期摄取清单有 N 项可能该查更新了,需要就说『跑定期摄取』」。**只提醒,不打断当前请求、不自动跑。**

**全局上下文(每次必读)**:读知识库数据目录下的 `context.md` —— 使用者主动维护的全局工作上下文(当前重点 / 主管方向 / 约束 / 背景,见 DESIGN.md §10)。把它作为本次 digest / search / brief / dashboard 的**「透镜」**:digest 时影响怎么解读、什么值得消化;search / brief 时在客观答案旁带出使用者视角与主管方向,并在**客观信息与某条陈述意图冲突时主动提示**。`context.md` 的内容呈现给用户时一律标为「你的视角 / 主管方向」,**非事实**;它是真相源 —— **本流程(操作前必读 / digest / search 等)中只读、绝不改写**;用户要增删改它走子命令 `context`(对话式 / OpenClaw 用户由 agent 代维护,见「context」一节)。`context.md` 不存在 → 用 skill 目录的 `templates/context.md` 初始化一个(整份复制、静默;统一模板避免格式漂移),本次视为空继续。

**其他**

- **schema 真相源是 [`DESIGN.md`](DESIGN.md)** —— 节点类型、字段、命名规范、目录结构全部以它为准。任何读写前先读 DESIGN.md。
- 知识库是一张实体图:7 类节点 —— `person` / `project` / `area` / `org` 是持续更新的**实体**,`event` / `decision` 是产生即定型的**记录**,`reading` 是外部读物(blog/论文/wiki 的思路库)—— 通过 frontmatter 的 `links` 互链。
- 节点骨架在 [`templates/`](templates/) 下 `node-<type>.md`。

## 安全约束(必须遵守)

- **逻辑与数据严格分离**:本 skill 仓库只含 agent 逻辑(可放 GitHub);知识库数据目录含**公司机密工作内容**。
- **绝不**把任何业务数据(节点 md、raw_data、journal、INDEX)写进本 skill 目录;数据一律写到知识库数据目录。
- 知识库数据目录**绝不**纳入本 skill 仓库的 git,**绝不** push 到任何 remote,不得外传。
- **不调用 lark-task 创建任务** —— 会议待办仅以 md 形式记录在 `event` 节点的"待办事项"章节内。

---

## digest — 摄取

**触发**:子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、或本地 md 路径,说"存入知识库""消化这个""记一下""把 XX 群最近的讨论存进来"。**不带来源**的 `digest`、或"跑定期摄取""检查周报更新" → 运行定期摄取(见 `references/digest-routine.md`)。

1. **分类** —— 判定 `source_type`:`feishu_doc` / `feishu_minutes` / `feishu_meeting` / `feishu_chat` / `web` / `local_md`。**若输入是一整场会议**(日历会议链接 / 日程,或同属一场会的投屏文档 + 妙记多个 URL)→ 这是「会议簇」,整体摄取成一个 event,见下方场景细则。
2. **摄取原文**:
   - `feishu_doc` → 用 `lark-doc +fetch --api-version v2` 读取文档正文。**摄取前必读** `references/digest-doc.md`。
   - `feishu_minutes` → 用 `lark-minutes` 取纪要、AI 产物(总结/待办/章节)、逐字稿。
   - `feishu_meeting` → 用 `lark-vc` 取会议纪要产物。
   - `feishu_chat` → 运行 `bin/pull-chat.sh` 拉取群聊(底层调 lark-im,自动定位群 + 分页拉全 + 输出逐字转写)。**摄取前必读** `references/digest-chat.md`。
   - `web` → 外部读物(blog/论文/wiki):用 WebFetch 抓网页正文,本地 PDF 用 Read。**摄取前必读** `references/digest-reading.md`。
   - `local_md` → 直接 Read。
   失败按下方「错误处理」中止。
3. **落原文** —— 写 `raw_data/<YYYY-MM-DD>-<slug>.md`:逐字原文 + frontmatter(`digest_status: pending`)。**raw_data 一旦写入永不改写。**
4. **冲突检测** —— 先确认 INDEX 一致(见「写入规范」);按标题/人名/项目名在 INDEX 找可能涉及的已有节点,Read 候选,语义比对是否与新输入矛盾。**有冲突 → 高亮矛盾点,等用户裁决,不静默覆盖。**
5. **digest 扇出**(DESIGN.md §4.3):
   - 必产 1 个主记录节点(会议、群聊窗口 → `event`;外部读物 → `reading`,不走 decision/实体扇出)。**会议簇**(同一场会的日历 + 投屏文档 + 妙记)仍只产 1 个 `event`,不按物件拆 —— 见 `references/digest-meeting.md`。
   - 抽取 N 个 `decision`:输入中每个明确决策一节点。
   - 创建或更新涉及的实体节点(`person`/`project`/`org`/`area`)。
   - **实体消解**(DESIGN.md §4.3):建实体前在 INDEX 比对,命中则更新而非新建。`person` **优先按 `feishu_id` 比对**(飞书英文 id、全局唯一;摄取文档时由 `bin/resolve-users.sh` 解析,写进 person frontmatter `feishu_id`)。**同名陷阱** —— 中文名相同但 `feishu_id` 不同 = 不同的人,**不合并**、**向用户确认后**各自建节点;`feishu_id` 拿不到而 KB 有同名 person → 提示用户确认是否同一人。`project`/`org`/`area` 按名比对,有歧义问用户。
   - **参与方立场分析**(DESIGN.md §4.5):`event` 除字面结论外,对每个关键参与方分析其立场、利益/动机、对决策的态度,并沉淀进对应 `person` 节点。**必须基于发言证据**,区分【观察】与【推断】,证据不足标「证据有限」,**不做无证据的发散猜测**。
   - **思路与视角沉淀**(DESIGN.md §4.6):摄取时若有人(使用者/主管/同事)陈述了对某 `project`/`area` 的思路、想法、打法或意图 → 在该节点「思路与视角」章节追加一条带日期、带作者、带【主张】/【意图】标记的条目(新条目在前)。第一方陈述用【主张】/【意图】,从发言推断仍用【推断】;**绝不把主观意图当成客观结论**。跨主题、不挂某个项目的工作底色不进节点,留给使用者维护 `context.md`。
   - **结合 `context.md` 重点关注**(操作前必读已把 `context.md` 当透镜加载):凡文档涉及 `context.md` 里记录的**使用者本人、其项目 / 团队、其关注的人(如直属领导)及这些人的指令 / 表态** —— 重点抽取、确保进入相应节点,不淡化、不漏。
   - **重点高亮**:文档若提到**重大事故、指标重大变化、或其它需要 highlight 的内容** → 在对应节点**显著记录**(如 `event` 的「结论」、`project` 的「关键进展 / 问题 / 风险」),并在第 7 步汇报时**单独、突出**地提醒用户。
6. **写入** —— 每个节点按 `templates/node-<type>.md` 骨架生成,遵守「写入规范」。
7. **汇报** —— 告诉用户:新建了哪些节点、更新了哪些、是否有冲突待裁决。**若本次命中「重点高亮」内容(重大事故 / 指标剧变 / 涉及你或你关注的人的重要指令等)→ 单独、显眼地提醒,别混在普通汇报里一笔带过。**

> **规模预估(大型输入必做)**:若输入很大(长文档、跨多业务/多表格、引用大量子文档),digest 前先预估本次会新建/更新约多少节点、牵出哪些子文档,告诉用户并确认摄取深度 —— **不无差别一次性铺开**。

### digest 分场景细则(动手前按需必读)

digest 的深层规则按场景拆到了 skill 目录 `references/` 下。**对应场景动手前必读对应文件 —— 漏读大概率做错**(漏增量高水位、漏滚动周期规则、漏子 agent 委派等):

| 场景 | 必读 |
|------|------|
| 摄取群聊(`feishu_chat`) | `references/digest-chat.md` |
| 摄取飞书文档(`feishu_doc`) | `references/digest-doc.md` |
| 摄取外部读物(`web`) | `references/digest-reading.md` |
| 摄取一场会议(日历会议 / 投屏文档 + 妙记 同属一场会) | `references/digest-meeting.md` |
| 输入大(长文档 / 滚动周报 / 大群聊窗口,或规模预估提示要读大量正文) | 加读 `references/digest-large.md` —— 委派子 agent 在隔离上下文里摄取 |
| 不带来源的 `digest` / "跑定期摄取" / "检查周报更新" | `references/digest-routine.md` |

`feishu_minutes` / `feishu_meeting` 单独摄取无额外细则(但若它属于一场带投屏文档的会议 → 走上面「会议簇」行);`local_md` 直接 Read —— 按上面主干执行即可。

## search — 查询

**触发**:子命令 `search`;或自然语言 —— "关于X我知道什么""我们关于Y定过什么""Z项目现在怎样"。

1. **双路召回候选节点**(互补,都要做):
   - **语义召回**:扫 `INDEX.md`,按 id / 标题 / **TL;DR** / tag / 人名,凭语义判断哪些节点可能相关 —— 不限字面命中。Claude 自身就是语义匹配器,INDEX 的 `TL;DR` 列就是为它铺的语义面。
   - **全文召回**:对查询关键词在知识库数据目录下跑 `grep -ri "<词>" knowledge/`,捞出 body 命中、但标题/TL;DR 未体现的节点 —— grep 即全文索引。
   - 两路结果取并集作候选节点。
2. **图遍历(检索承重墙,必做)** —— 候选 → 定向 Read 节点 → **必沿 `links` 遍历相关节点**,而不止于召回命中。关系型问题(「X 涉及哪些人」「Y 项目相关哪些决策」「谁参与了 Z」)尤其依赖此步,只看召回会漏掉一跳之外的关联。综合遍历到的节点回答。**答案必须附 `sources` 出处。**
3. **置信度(必报)**:
   - **高**:命中 ≥1 条 `status: current` 且 `last_verified` 在 90 天内的直接相关节点。
   - **中**:命中,但节点 `stale` / 超 90 天 / 仅 tag 间接相关。
   - **低 / 未命中**:执行**漏查防护** —— 二次放宽检索(换近义词重跑 `grep`、放宽到 tag 与邻接领域、扫 journal 近期记录),报告"已检索 N 个方向,未命中;主题接近的有……",**明确区分「知识库确实没有」与「我可能没找到」**。
4. 输出格式:先 TL;DR,再展开;末尾标注置信度 + 出处。

## update — 更新

**触发**:子命令 `update`;或自然语言 —— "更新X""X有新进展"。

1. 定位目标节点(查 INDEX)。
2. 若用户带来新输入 → 先按 `digest` 流程摄取为 raw。
3. 冲突检测(同 `digest` 第 4 步)。
4. **合并** —— 多源不一致时,以更晚的来源为准;旧值移入节点的「历史」章节并标注来源 + 日期,**不静默丢弃**。决策被取代 → 旧 `decision` 设 `status: superseded` 与 `superseded_by`。
5. 刷新 `updated` 与 `last_verified`;更新 INDEX、追加 journal。

## brief — 会前简报

**触发**:子命令 `brief`;或自然语言 —— "准备下个会""今天会议简报""会前简报"。

1. 用 `lark-calendar`(`+agenda`)取日程。日历调用失败 → 明确告知,不静默。
2. 对每个会议:提取主题、参会人。
3. 用主题词/人名查知识库(同 `search` 的检索)。
4. 每个会议生成简报:相关 `project`/`decision`/`person` 节点的 TL;DR + 出处。无相关条目 → 明说"该会议在库中无相关上下文"。
5. 这是用户触发的拉取式,**不做后台推送**。

## dashboard — 工作看板

**触发**:子命令 `dashboard`;或自然语言 —— "看板""今天进展""我要长期关注 X""提醒我关注 Y"。

看板文件 = 知识库数据目录下的 `dashboard.md`(与 `INDEX.md` 并列)。它是**实时视图**:📌 固定段由用户掌控,其余每次刷新重算 —— 看板不会过时。结构见 DESIGN.md §9。

**查看 / 刷新看板**:
1. `dashboard.md` 不存在 → 按 DESIGN.md §9 初始化。
2. 刷新派生内容:
   - 📌 长期关注:每个绑定了节点的关注项,从该节点拉最新 TL;DR/状态填"当前状态"列;自由文本项原样保留。
   - ⚠️ 需要关注:跑一次轻量扫描 —— 扫节点 frontmatter,标出 `last_verified` 超 90 天或 `status: stale` 的节点、未裁决冲突;手动提醒项原样保留。
   - 📅 今日进展:从当天 `journal/` 渲染(本库操作 + 用户报告的进展)。
   - 更新"最后刷新"时间戳。
3. 输出看板。

**长期关注 增/删**(用户说"长期关注 X"):能定位到知识节点 → 记 `节点 id + 关注什么`;定位不到 → 存自由文本 + 提示"摄取相关资料建节点后,看板就能自动拉状态"。写入/移除 📌 段对应行。

**加今日进展 / 加提醒**:今日进展 → 写一行到当天 `journal/`(durable),刷新时自动渲染进 📅;提醒 → 写入 ⚠️ 段手动项。

**跨天**:📅 今日进展不独立存储,刷新时按当天 journal 渲染 —— 跨天自动重置,历史在 journal。

写操作遵守下方「写入规范」。

## context — 全局工作上下文维护

**触发**:子命令 `context`;或自然语言 —— 用户要看或增删改自己的工作上下文,如"看一下我的工作上下文""更新下 context""把『主管说本季度重点是 X』记进去""我的当前重点改成 Y""删掉那条过期的约束"。

`context.md`(DESIGN.md §10)是使用者维护的全局工作上下文。**完全通过 OpenClaw 等对话式 agent 使用本 skill 的用户没法直接编辑文件 —— 必须由 agent 代为维护。**

**铁律 —— 区分「自动」与「受命」**:
- digest / search / brief / dashboard 等流程中,agent **永不擅自改动 `context.md`**(它只被当「透镜」读)。
- 仅当用户**明确要求**增 / 改 / 删某条上下文时,agent 才按本节代为编辑。

**查看**:用户只想看 → 读 `context.md`,把内容回显给用户(对话式用户看不到文件)。

**维护**(用户要增 / 改 / 删):
1. 读 `context.md`(「操作前必读」已确保它存在、且按 `templates/context.md` 初始化过)。
2. 判断动哪个章节(`我的当前重点` / `主管方向` / `当前约束` / `背景信息`);拿不准就问用户,不擅自归类。
3. 按模板格式 `- <YYYY-MM-DD> —— <一句话>` 增 / 改 / 删条目;**四个章节名与 `<!-- 指引 -->` 注释保持不动**,只动条目。日期用当天。
4. 保持简短 —— 发现明显过期的旧条目,**提示用户**是否一并清掉,不擅自删。
5. 原子写入(temp-then-move)。
6. **回显**:把改完后相关章节的内容呈现给用户确认 —— 对话式用户没有别的途径看到结果。
7. 知识库数据目录 `git add -A && git commit`(回滚点);journal 追加一行。

## help — 帮助

**触发**:子命令 `help`;或自然语言 —— "帮助""byteworker 怎么用""这个 skill 能做什么""用法"。

不读写知识库 —— 读 skill 目录的 `references/help.md`,把其全部内容**原样**输出给用户(放进代码块呈现)。

---

## 写入规范(所有写操作通用)

- 节点文件按 `templates/node-<type>.md` 骨架;生成时**删除** `<!-- 指引 -->` 注释。
- **原子写入**:先写 `<file>.tmp` → 校验 frontmatter 完整 → move 覆盖,避免半成品。
- **双向 links**:写 A→B 链接,必同时在 B 的 `links` 写回 A。
- **自动连边(auto-link)**:写节点 body 时扫描正文,凡出现其它节点 id(形如 `person-xxx`、`project-xxx` 等 7 类前缀)且该 id 在 INDEX 中确实存在的,自动并入本节点 `links` 并双向写回 —— 不依赖 digest 时主动想起,避免漏连。
- **INDEX 增量更新**:写/改节点后更新 `INDEX.md` 对应行,不每次全扫。若发现某类 `knowledge/<type>/` 文件数 ≠ INDEX 该节行数 → 全量重建(见下方「重建与恢复」)。
- **journal**:每次摄取/更新/看板写操作后,向 `journal/<YYYY-MM>/<YYYY-MM-DD>.md` 追加一行 —— 时刻、动作、触达节点 id、raw_id、是否冲突。
- **回滚点**:每次写操作完成后,在知识库数据目录执行 `git add -A && git commit`(该目录自身的本地 git,**永不 push**),使每一步可回滚。
- **命名 / 字段**:严格按 DESIGN.md §2(命名)与 §4.1(字段)。
- 单类节点 > 200 条 → 提示用户该类按子目录分片(暂不自动做)。

## 重建与恢复

**数据不变量(DESIGN.md §1.C)**:`raw_data/` + `knowledge/` 节点 + `dashboard.md` 的 📌/⚠️ 手动项 = **真相源**;`INDEX.md` 与 `dashboard.md` 的派生部分 = **派生物**,可随时丢弃并从真相源 100% 重建。派生物与真相源不一致时,**永远重建派生物,绝不反向改真相源**。

**重建 INDEX(一等操作)** —— 不只在「文件数 ≠ 行数」时兜底触发;任何时候用户说「重建索引」「INDEX 不对」,或你怀疑 INDEX 与节点不一致,都可直接执行:
1. 扫 `knowledge/` 下全部 7 类节点的 frontmatter + body 首行 TL;DR。
2. 按 DESIGN.md §6 的分节格式(含 `TL;DR` 列)重新生成整个 `INDEX.md`;「待消化」表 = 扫 `raw_data/` 中 `digest_status: pending` 的文件;「群聊摄取进度」表 = 扫 `raw_data/` 的 `feishu_chat` raw frontmatter,按 `source_chat_id` 聚合、取最近 `source_window`。
3. 原子写入(temp-then-move),追加一行 journal。

**灾难恢复** —— 数据目录是独立本地 git(DESIGN.md §1.B):
- 误删 / 错改某节点 → `git restore <文件>` 或 `git checkout <commit> -- <文件>` 回滚。
- `INDEX.md` 损坏 / 丢失 → 直接「重建 INDEX」,无需动 git。
- 数据目录大范围损坏 → `git reflog` 找最近完好提交回滚(每次写操作都有提交,见「写入规范」回滚点)。

## 错误处理(摄取管线)

| 失败 | 处理 | 用户看到 |
|------|------|----------|
| 无权限 | 中止,不写 raw_data | "无权限访问该文档/会议,请检查共享设置" |
| 资源已删 / URL 失效 | 中止 | "该链接已失效或被删除" |
| 会议未结束 / 无纪要产物 | 中止,提示稍后 | "会议纪要尚未生成,请会后重试" |
| 网络 / 超时 | 重试 1 次,再失败则中止 | "拉取超时,已重试一次,请稍后再试" |
| 输入类型无法判定 | 实体类倾向 `area`、记录类倾向 `event` 兜底,journal 标注 | "未识别输入类型,已用兜底处理" |
| 写入中断 | temp-then-move 保证原子性,清理残留 `.tmp` | "写入失败,知识库未改动" |
| INDEX 文件数 ≠ 行数 | 全量重建 | "检测到索引不一致,已重建" |

> LLM digest 有丢事实/幻觉风险:`raw_data` 逐字保留 + 节点 `sources` 溯源,
> 任何答案都可回原文核对。digest 时不确定的内容宁可标注存疑,不臆造。
