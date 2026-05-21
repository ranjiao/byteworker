---
name: byteworker
description: 个人飞书工作知识库。把飞书文档、会议妙记、会议、群聊、外部 blog/论文/wiki、本地 md 摄取(digest)并消化成结构化实体图笔记(人员/项目/主题领域/组织/事件/决策/读物),支持对话式查询(search)、更新(update)、会前简报(brief)、工作看板(dashboard)。当用户要把飞书文档/妙记 URL/会议/群聊/外部文章/本地 md 存入知识库、消化某份资料或某个群的讨论、查询"关于X我知道什么"或"我们关于Y定过什么"、查项目进度、更新某条知识、要会前简报、查看工作看板、或想了解这个 skill 怎么用(help)时使用;支持 /byteworker <digest|search|update|brief|dashboard|help> 子命令。
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
| `help` | 帮助 | 用法说明 | `/byteworker help` |

带子命令时按对应能力执行;未带子命令的自然语言输入,按各能力的「触发」判断。

## 操作前必读

**自动更新(最先做)**:每次使用本 skill,先静默运行 `bin/update-check.sh` —— 它每周最多实际检查一次,从 GitHub fast-forward 拉取最新 skill 内容。脚本**有输出**就把那一行转告用户,**无输出**则静默继续(更新于下次使用生效)。

**本 skill 只含 agent 逻辑。业务数据(知识库内容)存在独立的「知识库数据目录」,不在本 skill 仓库内,也不进本仓库的 git。**

- 读本 skill 目录下的 `.kbconfig`(已 gitignore),其中一行是知识库数据目录的绝对路径。
- 若 `.kbconfig` 不存在(**首次使用**):**主动询问用户**知识库数据目录放在哪里 —— 让用户给一个父目录,目录名默认 `byteworker_kb`(用户可改);拼出绝对路径后写入 `.kbconfig`。
- 若该数据目录不存在或为空:按 DESIGN.md §1.B 初始化 —— 创建 `knowledge/` 的 6 个子目录、`raw_data/`、`journal/`、空 `INDEX.md`,并对该数据目录执行 `git init`(**仅本地、永不配 remote**,作误删/错改的回滚网)。
- **下文所有 `knowledge/`、`raw_data/`、`journal/`、`INDEX.md`、`dashboard.md` 路径,一律指知识库数据目录下的对应路径;`templates/` 与 `DESIGN.md` 在本 skill 目录下。**

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

**触发**:子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、或本地 md 路径,说"存入知识库""消化这个""记一下""把 XX 群最近的讨论存进来"。

1. **分类** —— 判定 `source_type`:`feishu_doc` / `feishu_minutes` / `feishu_meeting` / `feishu_chat` / `web` / `local_md`。
2. **摄取原文**:
   - `feishu_doc` → 用 `lark-doc +fetch --api-version v2` 读取文档正文。详见下方「文档摄取补充」。
   - `feishu_minutes` → 用 `lark-minutes` 取纪要、AI 产物(总结/待办/章节)、逐字稿。
   - `feishu_meeting` → 用 `lark-vc` 取会议纪要产物。
   - `feishu_chat` → 运行 `bin/pull-chat.sh` 拉取群聊(底层调 lark-im,自动定位群 + 分页拉全 + 输出逐字转写)。详见下方「群聊摄取补充」。
   - `web` → 外部读物(blog/论文/wiki):用 WebFetch 抓网页正文,本地 PDF 用 Read。详见下方「读物摄取补充」。
   - `local_md` → 直接 Read。
   失败按下方「错误处理」中止。
3. **落原文** —— 写 `raw_data/<YYYY-MM-DD>-<slug>.md`:逐字原文 + frontmatter(`digest_status: pending`)。**raw_data 一旦写入永不改写。**
4. **冲突检测** —— 先确认 INDEX 一致(见「写入规范」);按标题/人名/项目名在 INDEX 找可能涉及的已有节点,Read 候选,语义比对是否与新输入矛盾。**有冲突 → 高亮矛盾点,等用户裁决,不静默覆盖。**
5. **digest 扇出**(DESIGN.md §4.3):
   - 必产 1 个主记录节点(会议、群聊窗口 → `event`;外部读物 → `reading`,不走 decision/实体扇出)。
   - 抽取 N 个 `decision`:输入中每个明确决策一节点。
   - 创建或更新涉及的实体节点(`person`/`project`/`org`/`area`)。
   - **实体消解**:建实体前在 INDEX 按名比对,命中已有节点则更新而非新建;有歧义问用户。
   - **参与方立场分析**(DESIGN.md §4.5):`event` 除字面结论外,对每个关键参与方分析其立场、利益/动机、对决策的态度,并沉淀进对应 `person` 节点。**必须基于发言证据**,区分【观察】与【推断】,证据不足标「证据有限」,**不做无证据的发散猜测**。
6. **写入** —— 每个节点按 `templates/node-<type>.md` 骨架生成,遵守「写入规范」。
7. **汇报** —— 告诉用户:新建了哪些节点、更新了哪些、是否有冲突待裁决。

> **规模预估(大型输入必做)**:若输入很大(长文档、跨多业务/多表格、引用大量子文档),digest 前先预估本次会新建/更新约多少节点、牵出哪些子文档,告诉用户并确认摄取深度 —— **不无差别一次性铺开**。

### 群聊摄取补充

群聊是连续消息流,摄取规则与文档/会议不同:

- **时间窗(必须)**:摄取前先确定窗口 —— 用户给了范围则用之;用户说"自上次以来"则扫 `journal/` 找该 `chat_id` 上一次 `feishu_chat` 摄取的时间作起点;都没有则默认最近 7 天并与用户确认。
- **拉取**:运行 `bin/pull-chat.sh --query "<群名>" --start <ISO8601> --end <ISO8601>`(已知 chat_id 则用 `--chat-id <oc_xxx>`)。脚本自动定位群、分页拉全,把逐字转写写到文件,并在 stdout 打印 `chat_id` / `chat_name` / `messages` / `transcript` 路径。**不要手写分页循环。** 若脚本报"匹配到多个群",改用 `--chat-id` 重跑。
- **落原文**:raw_data 用 `feishu_chat` 变体 frontmatter(`source_chat_id` / `source_chat_name` / `source_window`,见 DESIGN.md §3)。
- **强过滤**:群聊约 90% 是噪音。digest 只抽取 **决策、状态变化、关键结论、待办、重要问答**;丢弃寒暄、表情、刷屏、无信息量消息。窗口内无值得入库的内容 → 明确告诉用户"该窗口无可入库内容",**不硬造节点**。
- **扇出**:1 个 `event`(该群该窗口的讨论快照)+ N 个 `decision` + 更新涉及的 `project`/`person`/`org`/`area`。窗口若含多个明显无关的大讨论,可拆成多个 `event`。

### 文档摄取补充

飞书文档(尤其调研/规划类)常是「枢纽文档」,摄取规则:

- **人员 @ 提及解析**:`lark-doc` 返回的 `<cite type="user">` 是裸 `open_id`(不像群聊会自动解析姓名)。digest 前运行 `bin/resolve-users.sh --from-doc <原文文件>`(或 `--ids ou_x,ou_y`)拿到 `open_id → 姓名` 映射,再建/更新 `person` 节点。**不要手写解析逻辑。**
- **嵌入电子表格 / 多维表格**:文档里的 `<sheet>` / bitable 只返回占位 token,**关键数据在表格内**。需要这些数据时用 `lark-sheets` / `lark-base` 下钻取数;不下钻则在「关联文档与会议」登记该表并标注"数据在表格内"。
- **引用的子文档**:文档里 `<cite type=doc>` 引用的其他文档 → **登记进项目节点的「关联文档与会议」**;**不自动递归摄取**(会爆炸),而是把这些子文档列给用户,由用户决定是否进一步摄取。

### 读物摄取补充

外部读物(blog / 论文 / wiki)弱相关于工作,摄取规则:

- **抓取**:web URL → `WebFetch` 取正文;本地 PDF / 文章 → `Read`。
- **digest**:一篇文章 → **1 个 `reading` 节点**(写入 `knowledge/readings/`),提炼 核心观点 + 可借鉴点(对工作的潜在启发)。**不产 event/decision,一般不动工作实体节点。**
- **链接**:摄取时若文章明显关联某 `area`/`project`,可连 `links`;否则留空 —— 关联靠日后工作引用时再长出来。
- `reading` 低维护:观点不过期,`status` 恒为 `current`,不进新鲜度/看板 ⚠️ 逻辑。

## search — 查询

**触发**:子命令 `search`;或自然语言 —— "关于X我知道什么""我们关于Y定过什么""Z项目现在怎样"。

1. 扫 `INDEX.md` 找命中节点(按 id / 标题 / tag / 人名)。
2. 命中 → 定向 Read 节点 → 沿 `links` 拉相关节点 → 综合回答。**答案必须附 `sources` 出处。**
3. **置信度(必报)**:
   - **高**:命中 ≥1 条 `status: current` 且 `last_verified` 在 90 天内的直接相关节点。
   - **中**:命中,但节点 `stale` / 超 90 天 / 仅 tag 间接相关。
   - **低 / 未命中**:执行**漏查防护** —— 二次放宽检索(tag 与邻接领域 + 扫 journal 近期记录),报告"已检索 N 个方向,未命中;主题接近的有……",**明确区分「知识库确实没有」与「我可能没找到」**。
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

## help — 帮助

**触发**:子命令 `help`;或自然语言 —— "帮助""byteworker 怎么用""这个 skill 能做什么""用法"。

不读写知识库,直接输出下面的使用说明(原样呈现):

```
byteworker 个人知识库 —— 用法

用法:/byteworker <子命令> [参数],或直接自然语言。

digest     摄取 —— 把资料存进知识库
  /byteworker digest <飞书文档/妙记 URL | 会议 | 群 | 外部 blog/论文 | 本地 md>
  也可:"把这个文档存进知识库 <URL>" / "把『XX群』最近一周的讨论存进来"
  → 拉取原文 → 消化成 人员/项目/主题领域/组织/事件/决策 节点 → 入库

search     查询 —— 问知识库
  /byteworker search 关于张三我知道什么 / 我们关于X定过什么 / Y项目现在怎样
  → 答案 + 出处 + 置信度(高 / 中 / 低-未命中)

update     更新 —— 知识有新进展
  /byteworker update 更新一下Y项目 / X决策有变动 / 这条我重新核实过了
  → 定位节点 → 合并新信息 → 旧值进「历史」→ 刷新核实日期

brief      会前简报 —— 开会前拉相关上下文
  /byteworker brief
  → 读飞书日历 → 每个会议生成相关知识简报

dashboard  工作看板 —— 看当下该关注什么
  /byteworker dashboard / 长期关注X / 提醒我关注Y
  → 长期关注项(自动拉最新状态)+ 需关注事项 + 今日进展

help       用法说明

存储:知识库数据目录(用户指定,独立于本 skill,不进 git)——
      knowledge/(节点)· raw_data/(原始输入)· journal/(日志)· INDEX.md · dashboard.md
文档:DESIGN.md(存储 schema)· TODOS.md(延后功能)
安全:数据含机密内容,绝不外传、绝不进 skill 仓库的 git
```

---

## 写入规范(所有写操作通用)

- 节点文件按 `templates/node-<type>.md` 骨架;生成时**删除** `<!-- 指引 -->` 注释。
- **原子写入**:先写 `<file>.tmp` → 校验 frontmatter 完整 → move 覆盖,避免半成品。
- **双向 links**:写 A→B 链接,必同时在 B 的 `links` 写回 A。
- **INDEX 增量更新**:写/改节点后更新 `INDEX.md` 对应行,不每次全扫。若发现某类 `knowledge/<type>/` 文件数 ≠ INDEX 该节行数 → 全量重建 INDEX(扫全部 frontmatter 重新生成)。
- **journal**:每次摄取/更新/看板写操作后,向 `journal/<YYYY-MM>/<YYYY-MM-DD>.md` 追加一行 —— 时刻、动作、触达节点 id、raw_id、是否冲突。
- **回滚点**:每次写操作完成后,在知识库数据目录执行 `git add -A && git commit`(该目录自身的本地 git,**永不 push**),使每一步可回滚。
- **命名 / 字段**:严格按 DESIGN.md §2(命名)与 §4.1(字段)。
- 单类节点 > 200 条 → 提示用户该类按子目录分片(暂不自动做)。

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
