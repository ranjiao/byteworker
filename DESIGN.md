# byteworker 知识库 — 存储结构与字段设计

> 本文档锁定「存什么、存成什么格式、字段怎么设计」。SKILL.md 与 templates/ 按此实现。
> 来源:CEO 评审 2026-05-20(SCOPE EXPANSION 模式);2026-05-20 改为实体图模型。

---

## 0. 核心模型:实体图

知识库是一张**实体图**。不再用会漂移的「topic 分类」,而用 **7 类实体/记录节点**,
知识持续累积到节点上,节点之间互相链接。

**实体(持续更新的活节点):**
| 类型 | id 前缀 | 节点上累积什么 |
|------|---------|----------------|
| 人员 `person` | `person-` | 角色、负责什么、协作历史、偏好、关键交互 |
| 项目 `project` | `project-` | 状态、范围、里程碑、风险、关联决策。**广义=有生命周期的专项/事项**(大促、事故等持续事项也归这里) |
| 主题领域 `area` | `area-` | 常青参考知识、规范、how-to、踩坑 |
| 组织 `org` | `org-` | 团队/供应商的职责、成员、对接方式、协作历史 |

**记录(产生即定型的节点):**
| 类型 | id 前缀 | 内容 |
|------|---------|------|
| 事件 `event` | `event-` | 一次会议/评审/发布的 digest 快照,定型;链接到人/项目/组织 |
| 决策 `decision` | `decision-` | 一个决策及理由/相关方/影响;可被新决策 supersede;链接到项目/事件/人 |
| 读物 / 资料卡 `reading` | `reading-` | 外部 blog/论文/wiki,以及内部路线思考/方法论/调研/技术白皮书的 digest:核心观点 + 方法框架 + 可借鉴点 |

**图的边:** event 链接它涉及的 person/project/org;decision 链接相关 project/event 与决策人;
project 链接成员 person、所属 area、所属 org;reading 作为资料入口链接它支撑或影响的 project/area/decision/event。
查询「关于张三我都知道什么」= person-zhang-san 节点 + 所有链回他的 event/decision/project。

---

## 1. 目录职责 — 逻辑与数据严格分离

byteworker 由**两个物理隔离**的部分组成。

### A. skill 仓库(纯 agent 逻辑,可进 git / GitHub)

| 文件/目录 | 存什么 |
|-----------|--------|
| `SKILL.md` | skill 行为定义(digest/search/update/brief/dashboard/daily/weekly/help) |
| `DESIGN.md` | 本文档:存储 schema |
| `templates/` | 7 类节点骨架模板 |
| `TODOS.md` / `CLAUDE.md` | 延后项 / 仓库须知 |
| `.kbconfig` | 知识库数据目录的绝对路径(**已 gitignore,不提交**) |

### B. 知识库数据目录(业务数据,用户指定,**绝不进 skill 仓库的 git**)

| 目录/文件 | 存什么 | 谁写 | 可变性 |
|-----------|--------|------|--------|
| `raw_data/` | 摄取的**逐字原文** + 溯源元数据,一次摄取一文件 | skill 写入,**永不改写** | 只增不改 |
| `knowledge/{people,projects,areas,orgs,events,decisions,readings}/` | 7 类节点笔记,按类型分子目录(固定 7 个,不漂移) | skill 写入/更新 | 实体可更新;记录定型 |
| `journal/` | 摄取/更新/扫描事件的**时间线日志** | skill 追加 | 只追加 |
| `reports/daily/`, `reports/weekly/` | 日报 / 周报归档快照,由 `daily` / `weekly` 生成 | skill 写入,用户可手改 | 可覆盖同周期 |
| `INDEX.md` | 主索引:7 类节点登记表 + 待消化表 | skill 维护,可全量重建 | 高频更新 |
| `dashboard.md` | 工作看板 —— 实时视图(长期关注 / 需关注 / 今日进展) | skill 维护/渲染 | 高频刷新 |
| `context.md` | 全局工作上下文 —— 使用者主动维护的「透镜」(当前重点 / 主管方向 / 约束 / 背景) | 用户手维护 | 手维护 |
| `.last-routine-digest` | 上次「定期摄取」例程运行日期(一行 `YYYY-MM-DD`)—— 到期提醒据此判断 | skill 写入 | 每次定期摄取覆盖 |

数据目录路径由用户首次使用时指定(默认目录名 `byteworker_kb`,路径可配置),
记于 skill 仓库的 `.kbconfig`(已 gitignore)。数据目录是**它自己的独立本地 git 仓库**
(作误删/错改的回滚网,**永不配 remote**),与 skill 仓库的 git 互不相干。
数据目录含**公司机密内容**,绝不外传、绝不纳入 skill 仓库的 git。

### C. 真相源 vs 派生 —— 数据不变量

知识库数据按「丢了能不能恢复」分两层,这是一条**硬不变量**:

**真相源(truth source —— 丢失不可恢复,必须保护):**
- `raw_data/` —— 不可变、逐字;一切知识的根。
- `knowledge/` 节点 —— 可变消化产物,承载真正的知识价值;节点出错可回对应 `raw_data`
  重新消化(LLM digest,非确定性),但 `raw_data` 本身丢了就无源可回。
- `reports/` —— 日报 / 周报是用户可手改的归档快照;同周期可重新生成,但需保留手动备注。
- `dashboard.md` 的 📌 长期关注列表 + ⚠️ 手动提醒 —— 用户状态,只此一处保存。
- `context.md` —— 使用者主动维护的全局工作上下文;手维护、不可派生,只此一处保存。

**纯派生(derived —— 可随时丢弃,必须 100% 可重建,不必单独备份):**
- `INDEX.md` —— 可从全部节点的 frontmatter + body 首行 TL;DR、加 `raw_data/` frontmatter **确定性**全量重建(见 §6)。
- `dashboard.md` 的派生部分 —— 关注项当前状态、⚠️ 派生项、📅 今日进展,每次刷新重算。

**推论(SKILL.md「重建与恢复」据此实现):**
- 派生物永远服从真相源 —— 两者不一致时,**以真相源为准、重建派生物**,绝不反向改真相源。
- 「重建 `INDEX.md`」是一等操作,不是兜底:任何时候怀疑 INDEX 不对 → 直接全量重建。
- 灾难恢复:数据目录有独立本地 git。误删/错改 → `git restore` / `git checkout` 回滚;
  仅 `INDEX.md` 损坏/丢失 → 重建即可,无需动 git。

---

## 2. 命名规范

- **slug**:取标题核心关键词 → 英文/拼音 kebab-case,≤40 字符;碰撞追加 `-2`/`-3`。
- **raw 文件**:`raw_data/<YYYY-MM-DD>-<slug>.md`,`raw_id` = `raw-<YYYY-MM-DD>-<slug>`。
- **节点文件 / id**:
  - 实体:`knowledge/<类型复数>/<前缀><slug>.md`,如 `project-q2-roadmap`、`area-rec-system`、`org-data-platform-team`。
    - `person` 与其它实体同规则:slug 取姓名核心关键词(英文 / 拼音 kebab-case),id `person-<slug>`、文件名同名。**id 一经生成永不改**(仅同名碰撞才追 `-2`/`-3`)。同名 / 同人消歧不靠 id,靠 frontmatter 的 `feishu_id` 字段(见 §4.1、§4.3);`feishu_id` 建节点时拿不到就先填 `?`,日后解析到了**回填该字段**即可 —— 纯字段编辑,不动 id、不改名、不级联。
  - 事件含日期:`event-<YYYY-MM-DD>-<slug>`,如 `event-2026-05-20-q2-review`。
  - 决策:`decision-<slug>`;读物:`reading-<slug>`。
- **journal**:`journal/<YYYY-MM>/<YYYY-MM-DD>.md`。
- **reports**:`reports/daily/<YYYY-MM-DD>.md`;`reports/weekly/<YYYY>-W<WW>.md`(ISO 周)。
- 单类节点 > 200 时再分子目录(TODOS)。

### 2.1 时间格式规范

知识库里所有**结构化时间**统一使用下面几种格式。原始正文(raw body)必须逐字保留,不因本规范改写;但 raw frontmatter、knowledge 节点、INDEX、journal、reports、dashboard 等由 skill 生成的内容必须规范化。

| 场景 | 格式 | 示例 | 说明 |
|------|------|------|------|
| 日期 | `YYYY-MM-DD` | `2026-05-21` | 默认格式;节点 frontmatter 的 `created` / `updated` / `last_verified`、正文条目日期、`.last-routine-digest` 均用它 |
| 带本地时间 | `YYYY-MM-DD HH:MM` | `2026-05-21 19:00` | 面向人读的正文 / journal / report 生成时间;默认 Asia/Shanghai,不写秒 |
| 完整时间戳 | `YYYY-MM-DDTHH:MM:SS+08:00` | `2026-05-21T19:00:41+08:00` | 机器边界字段,如 `raw_data.ingested`、`source_window`、群聊高水位;必须带时区 |
| 时间范围(人读) | `<start> .. <end>` | `2026-05-21 19:00 .. 20:31` | 同日范围可省略结束日期;跨日写完整日期 |
| 时间范围(机器) | `<ISO8601> .. <ISO8601>` | `2026-05-21T00:00:00+08:00 .. 2026-05-25T00:07:30+08:00` | `source_window` 等可续拉字段 |
| ISO 周 | `YYYY-Www` | `2026-W21` | 周报文件名、周报标题 |
| 月 | `YYYY-MM` | `2026-05` | journal 子目录名 |

规范化规则:
- 禁止在 skill 生成内容中写 `YYYYMMDD`、`M.D`、`5-21`、`05/21` 等裸格式;输入里出现这类周期时,消化后统一转成 `YYYY-MM-DD`。例如 `20260520` → `2026-05-20`,`5-21` 在已知年份为 2026 时 → `2026-05-21`。
- `digest_period` 若表示日期周期,统一写 `YYYY-MM-DD`;若表示 ISO 周,写 `YYYY-Www`;确实不是日期(如版本号 / 阶段名)才保留原样并在正文说明。
- `INDEX.md` 的 `last_verified`、定期摄取清单「上次摄取」、群聊摄取进度「已摄取至」必须使用上表格式:日期源用 `YYYY-MM-DD`,群聊高水位用完整时间戳。
- 节点 body 中带时间的条目开头优先使用 `- YYYY-MM-DD ...`;若需要具体时间,写 `- YYYY-MM-DD HH:MM ...`。`思路与视角` 固定为 `- 【主张|意图】<作者> · YYYY-MM-DD —— <内容>`。
- journal 行以 `- HH:MM ...` 开头,文件路径已提供日期;若引用外部事件发生时间,正文里仍写完整 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`。
- 报告顶部 `生成时间` 用 `YYYY-MM-DD HH:MM`;`范围` 用人读时间范围。

---

## 3. raw_data/ — 原始输入

每次摄取写一个文件,逐字保留,**不做任何改写/删减**。

```markdown
---
raw_id: raw-2026-05-20-q2-roadmap-review
ingested: 2026-05-20T14:30:00+08:00
source_type: feishu_doc | feishu_minutes | feishu_meeting | feishu_chat | web | local_md
source_url: https://<feishu-url>           # 本地 md 则填原路径
source_title: Q2 路线图评审会
digest_status: pending | digested | failed
routine: weekly                            # 可选:会定期更新的源(滚动周报/群聊)纳入定期摄取后才有
digest_targets:                            # 本次摄取触达的所有节点 id
  - event-2026-05-20-q2-review
  - decision-q2-scope
  - project-q2-roadmap
---

# Q2 路线图评审会

<逐字原文 / lark-minutes 纪要+逐字稿 / lark-doc 文档正文,原样粘贴>
```

**`feishu_chat` 变体**:群聊摄取按「群 + 时间窗」进行,**同一群可多次增量摄取**。
frontmatter 不用 `source_url` / `source_title`,改用 `source_chat_id`(oc_xxx)、
`source_chat_name`(群名)、`source_window`(本次摄取的消息时间窗,**完整 ISO8601 起止**,
如 `2026-05-15T00:00:00+08:00 .. 2026-05-21T18:00:00+08:00`)。`source_window` 的结束点
即该群「上次处理到哪」的**高水位** —— `bin/pull-chat.sh --since-last` 扫 `raw_data/` 取该
`chat_id` 最近一次 `source_window` 的结束时间,据此续拉下一窗口。`raw_id` 的 slug 取群名 +
窗口标识。正文为该窗口的逐字消息(发送人 · 时间 · 内容,原样)。

**`web` 变体**:外部读物(blog / 论文 / wiki)。`source_url` 填文章链接(本地 PDF 则填路径),
`source_title` 填文章标题。正文为宿主 agent 抓取/读取到的文章正文。

**`routine` 字段(可选)**:若来源是**会定期更新**的源(滚动周会文档、群聊等),经用户确认
纳入「定期摄取」后,frontmatter 加 `routine: weekly`(cadence,默认 `weekly`);该源后续每个
raw 都带此标记。INDEX 的「定期摄取清单」由扫描带 `routine` 的 raw 派生(§6),定期摄取例程据此
逐源增量 re-digest。详见 SKILL「定期摄取」。

---

## 4. knowledge/ — 节点笔记

### 4.1 通用 frontmatter(7 类都有)

```yaml
---
id: project-q2-roadmap
title: Q2 产品路线图
type: person | project | area | org | event | decision | reading
tags: [roadmap, q2]
status: current | stale | superseded         # 实体常为 current/stale;记录可 superseded
created: 2026-05-20
updated: 2026-05-20
last_verified: 2026-05-20                     # 新鲜度判断依据(看板 ⚠️ 段用)
superseded_by: decision-xxx                   # 仅 status=superseded
sources:                                      # 溯源:raw_id 或飞书原链接,≥1 条
  - raw-2026-05-20-q2-roadmap-review
links:                                        # 图的边,双向维护(写 A→B 同时在 B 写回 A)
  - person-zhang-san
  - area-product-planning
  - event-2026-05-20-q2-review
---
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✓ | `<前缀><slug>`,全局唯一 |
| `type` | ✓ | 7 类之一,决定子目录与 body 结构 |
| `tags` | ✓ | 自由二级标签,承载角色特异性(数据集名、渠道、技术栈…);优先复用已有 tag |
| `status` | ✓ | `current` / `stale` 疑似过期 / `superseded` 已被取代 |
| `created`/`updated`/`last_verified` | ✓ | 创建 / 最后修改 / 最后被新输入或人工确认的日期,格式固定为 `YYYY-MM-DD` |
| `superseded_by` | ✗ | 退役时指向取代它的节点 |
| `sources` | ✓ | 溯源根,指回 raw_data 或飞书链接 |
| `links` | ✗ | 关联节点 id,**双向维护**;id 前缀即对端类型;body 中提及的已存在节点 id 自动纳入(auto-link,见 SKILL.md 写入规范) |
| `feishu_id` | △ | **仅 `person`**:该人飞书英文 id(企业邮箱 `@` 前缀),全局唯一 —— person 实体消解的主键、用于消歧同名。**只是一个字段,不参与 id / slug**(id 规则见 §2)。摄取时由 `bin/resolve-users.sh` / lark-contact 解析,确实拿不到先填 `?`、日后解析到再回填 |

> 不再有 `topic` 字段——领域结构由 `area`/`org` 节点 + `links` 承载,topic 治理问题消解。

### 4.2 body 结构(按 type)

所有类型 body 首行统一 TL;DR(查询先返回它):
```markdown
# <title>

> **TL;DR:** <一句话摘要>
```

**`person`(实体)** —— 在 §4.1 通用 frontmatter 之外额外带 `feishu_id`(飞书英文 id,§4.1)。
```markdown
## 基本信息        <!-- 角色 / 所属团队 / 对接方式 -->
## 负责什么
## 协作历史与关键交互  <!-- 带时间条目按事件发生时间倒序 -->
## 立场 / 利益 / 动机   <!-- 跨讨论沉淀的立场倾向 / 核心诉求 / 行为逻辑;须有证据,见 §4.5 -->
## 偏好 / 风格 / 注意点
## 关联节点
```

**`project`(实体,广义专项/事项)**
```markdown
## 关联文档与会议   <!-- 该项目被讨论/提及的主要文档/会议/群聊(标题+日期+链接);按事件发生时间倒序、持续追加去重 -->
## 目标
## 关键策略
## 关键进展         <!-- 带日期,按事件发生时间倒序;含里程碑、关键决策、状态变化 -->
## 问题            <!-- 当前待解决的问题/阻塞 -->
## 风险            <!-- 潜在风险 -->
## 成员 / 相关方     <!-- person 链接 -->
## 思路与视角        <!-- 各方对本项目的主观思路/想法/打法/意图;第一方陈述,带日期带作者,标【主张】/【意图】,见 §4.6 -->
## 历史             <!-- 目标/策略被推翻时旧值移入,标来源+日期;按事件发生时间倒序 -->
```
> 一个项目会被多个文档/会议反复讨论:每次 digest 涉及该项目,都要把新来源追加进
> 「关联文档与会议」,并刷新 目标/关键策略/关键进展/问题/风险。**无信息的章节留空。**

**`area`(实体,主题领域常青知识)**
```markdown
## 概述 / 定义
## 关键知识点
## 规范 / 流程 / how-to
## 踩坑 / 注意事项
## 思路与视角        <!-- 各方对本领域的主观思路/想法/判断;第一方陈述,带日期带作者,标【主张】/【意图】,见 §4.6 -->
## 相关节点与外部链接
```

**`org`(实体,组织/团队/供应商)**
```markdown
## 基本信息        <!-- 内部团队 / 外部供应商;职责 -->
## 关键成员         <!-- person 链接 -->
## 对接方式 / 流程
## 协作历史         <!-- 带时间条目按事件发生时间倒序 -->
## 关联项目
```

**`event`(记录,产生即定型)**
```markdown
## 事件信息        <!-- 时间 / 类型:会议|评审|发布|群聊讨论窗口 / 参会人 -->
## 议程与讨论
## 结论
## 参与方立场分析   <!-- 各关键参与方的立场/动机/对决策态度;须基于证据,标【观察】/【推断】,见 §4.5 -->
## 重点事项        <!-- 和用户本人相关、重点关注项目、重要人物观点，以及其他在context.md里面要求关注的重点事项 -->
## 待办事项        <!-- 责任人 + 截止日期 -->
## 衍生与关联       <!-- 产生/更新的 decision、涉及的 project/person/org -->
```

**`decision`(记录,可被 supersede)**
```markdown
## 决定了什么
## 理由 / 背景
## 决策人 / 相关方
## 影响范围
## 当前状态        <!-- 生效中 / 待执行 / 已被取代 -->
## 关联节点         <!-- project / event / person -->
## 历史             <!-- 带时间条目按事件发生时间倒序 -->
```

**`reading`(记录,读物 / 资料卡 / 思路)**
```markdown
## 来源            <!-- 链接 / 作者 / 发布日期 / 类型:blog|论文|wiki|内部路线思考|方法论|调研|技术白皮书|复盘 -->
## 核心观点         <!-- 逐条提炼资料的关键观点、论点、方法、证据 -->
## 可借鉴点         <!-- 对工作的潜在启发(「思路」角度);无则留空 -->
## 相关节点         <!-- links;内部资料通常连到影响的 project/area/decision/event -->
```
> `reading` 是资料本身的 digest:外部读物通常弱相关于工作,默认**一篇文章一个 `reading` 节点**,
> 不走 event/decision 扇出;内部路线思考 / 方法论 / 调研 / 技术白皮书则以 `reading`
> 作为主记录,同时可按内容扇出明确 decision、更新相关 project/area/person/org。
> `reading` 低维护(观点不会像项目状态那样过期),`status` 基本恒为 `current`,不进看板陈旧告警。

### 4.3 一次摄取的产出(digest 扇出)

一次摄取(raw)按下面的**形状**扇出成多个节点 —— 这是实体图的生长方式:
1. **必产 1 个记录节点**:会议 / 群聊窗口 → `event`;外部读物、内部路线思考 / 方法论 / 调研 / 技术白皮书 → `reading`。
2. **抽取 N 个 `decision`**:输入中每个明确决策抽成独立节点。外部读物默认不走此步;内部资料型 `reading` 若包含明确生效的选择 / 原则 / 边界,可以抽 `decision`。
3. **创建或更新实体节点**:输入实质涉及的 person/project/org/area —— 不存在则建,已存在则
   走**实体消解**更新(建前在 INDEX 比对;`person` 优先按 `feishu_id`,见 §4.1)。
4. **全部互链** `links`(双向),并登记进 raw 的 `digest_targets`。

> 扇出的**行为细则**是 digest 流程、不在本文件:各 `source_type` 的差异、群聊强过滤与增量
> 语义、会议簇合并、实体消解的同名陷阱、立场与思路视角的沉淀 —— 见 `SKILL.md`「digest」
> 与 `references/digest-*.md`。本节只锁定扇出的形状。

### 4.4 什么该进知识库

**该存:** 决策与理由、项目/事项状态、常青参考知识、会议结论与待办、协作关系、外部读物与内部资料的观点 / 方法框架 / 可借鉴点。
**不该存:** 一周后即失效且无留存价值的琐碎、纯寒暄。
边界不清则 agent 高亮问用户,不静默丢弃也不硬塞。

**重度定量表格**(大型数据表 / 明细表):不强行复刻进 md 节点 —— 节点存
**结论 / 趋势 / 口径**,明细留在原文档 / 原表格,用「关联文档与会议」或 `sources` 链接回去。

### 4.5 参与方立场分析(书写准则见 references)

§4.2 已定义 `event` 的「参与方立场分析」章节:对关键参与方分析其立场 / 利益 / 动机,
结论同步沉淀进相关 `person` 的「立场 / 利益 / 动机」章节。它从会议发言**推断**而来(observed)。

**怎么写**(三维度、必须基于证据、【观察】/【推断】标记、证据不足写「证据有限」、不臆测)
是 digest 行为准则,统一收在 `references/digest-analysis.md`,由 `SKILL.md`「digest」路由读取。

### 4.6 思路与视角:第一方观点(书写准则见 references)

§4.2 已定义 `project` / `area` 的「思路与视角」章节:承载使用者 / 主管 / 同事**直接陈述**的
第一方观点(stated)—— 与 §4.5 的「推断」互补。带日期、带作者、只追加。

**怎么写**(【主张】/【意图】标记、作者标注、绝不硬化为事实、思路老化更快的处理)是 digest
行为准则,见 `references/digest-analysis.md`。与 `context.md`(§10)的分工:本章节挂在具体
`project`/`area` 上,`context.md` 是跨主题的工作底色。

---

## 5. journal/ — 时间线日志

`journal/<YYYY-MM>/<YYYY-MM-DD>.md`,按天追加,一事件一行:

```markdown
# 2026-05-20

- 14:30 摄取 [feishu_minutes] "Q2 路线图评审会" → 新建 event-2026-05-20-q2-review
  | 衍生 decision-q2-scope(新) | 更新 project-q2-roadmap、person-zhang-san
  | raw-2026-05-20-q2-roadmap-review
- 15:10 更新 decision-auth-approach ← raw-2026-05-20-auth-doc
  | 冲突:旧"方案A" vs 新"方案B" → 用户裁决"方案B",旧决策标 superseded
- 16:00 看板:记今日进展「与 X 对齐了下阶段排期」
```

每行含:时刻、动作、输入源、触达节点 id、raw_id、是否冲突。这是审计日志。

---

## 6. INDEX.md — 主索引

skill 自动维护,可从全部节点的 frontmatter + body 首行 TL;DR、加 `raw_data/` frontmatter 全量重建。
按 7 类分节,一行一节点:

```markdown
# 知识库索引

## 人员 (person)
| id | 标题 | feishu_id | TL;DR | status | last_verified |
|----|------|-----------|-------|--------|----------------|

## 项目 (project)
| id | 标题 | TL;DR | status | last_verified |

## 主题领域 (area) / 组织 (org) / 事件 (event) / 决策 (decision) / 读物 (reading)
| …同上… |

## 定期摄取清单 (routine digest — 会定期更新、需周期性复查的源)
| 源 | 类型 | cadence | 上次摄取 | 关联节点 |

## 群聊摄取进度 (feishu_chat 增量高水位)
| 群名 | chat_id | 已摄取至 | 最近 raw_id |
```

- **「定期摄取清单」表** = 会定期更新、需周期性复查的源(滚动周会文档、群聊等)。由扫描带 `routine` 标记的 `raw_data/` 文件派生(§3),一源一行,`上次摄取` = 该源最近 raw 的规范化周期 / 窗口:日期周期用 `YYYY-MM-DD`,ISO 周用 `YYYY-Www`,群聊窗口用完整高水位时间戳。「定期摄取」例程逐源增量 re-digest(见 SKILL「定期摄取」)。*替代了旧的「待消化」表 —— 后者无机制主动入列、形同虚设;`digest_status: pending/failed` 的中断 raw 改由扫 `raw_data/` 兜底发现。*
- **「群聊摄取进度」表** = 每个摄取过的群一行,记 `chat_id` 与「已摄取至」(该群最近一次 `source_window` 的结束点 = 增量高水位,格式固定为完整 ISO8601,如 `2026-05-25T00:07:30+08:00`)。digest 群聊前查此表判断首次 / 增量,摄取后更新对应行;从 `raw_data/` 的 `feishu_chat` raw frontmatter 派生、可重建。**这是 agent「这个群摄过没、摄到哪」的唯一可见入口。**
- **`TL;DR` 列** = 节点 body 首行的一句话摘要(§4.2)。让查询时的语义匹配作用在
  「标题 + 摘要」而非仅标题上,大幅提升语义召回 —— 这是 byteworker 不引入向量库
  也能做语义检索的关键:检索器是当前 agent/模型本身,只需把语义面在 INDEX 里铺够。
  摘要过长则截断到一行。
- **人员表的 `feishu_id` 列** —— 支持按飞书邮箱英文 id 直接检索到对应的人(node id 已与 `feishu_id` 解耦,见 §2;此列补回「按 id 找人」的便利)。
- 查询先扫 INDEX 再定向读取节点;写入时**增量更新**对应行,不每次全扫。
- 一致性兜底:某类 `knowledge/<类型>/` 文件数 ≠ INDEX 该节行数 → 触发全量重建。
  (纯内容编辑不改行数,无法靠计数发现 → 故增量更新是主路径。)
- 单类节点行数 > 200 → skill 必须提示该类按子目录分片(TODOS)。

---

## 7. templates/ — 节点骨架

```
templates/
  README.md            模板使用说明
  node-person.md       \
  node-project.md       \
  node-area.md           \  各 = §4.1 通用 frontmatter
  node-org.md            >  + §4.2 对应 type 的 body 章节
  node-event.md         /   + 章节内 <!-- 指引 --> 注释(填什么、从哪提取)
  node-decision.md     /
  node-reading.md
  context.md             context.md 文件骨架(全局上下文,§10;首次使用整份复制为初始 context.md)
  report-daily.md        日报骨架(daily 输出到 reports/daily/)
  report-weekly.md       周报骨架(weekly 输出到 reports/weekly/)
```
无法判定 type 时,实体类倾向 `area`、记录类倾向 `event`,并在 journal 标注。

---

## 8. 已锁定的决策

1. **领域分类** — 不预设 topic 清单;area/org 节点按需生长(实体图模型)。
2. **会议待办不接飞书任务** — `event` 的"待办事项"仅以 md 形式存在节点内;
   skill **不调用 lark-task 创建真实任务**。
3. **raw_data 永久保留** — v1 原始输入文件永久保留,不自动删除/归档;
   归档策略见 TODOS.md(P2,规模触发后再做)。
4. **逻辑与数据严格分离** — skill 仓库只含 agent 逻辑(可进 git/GitHub);所有业务数据
   (`knowledge/`、`raw_data/`、`journal/`、`INDEX.md`)存在用户指定的独立目录(默认名
   `byteworker_kb`),**绝不进 skill 仓库的 git**。数据目录路径记于 `.kbconfig`(gitignore)。
   数据目录有自己的**独立本地 git**(回滚用,永不 push),首次使用时由 skill 询问并初始化。
5. **新增并扩展 `reading` 节点类型** — 外部读物(blog/论文/wiki)与内部路线思考 / 方法论 /
   调研 / 技术白皮书的资料卡,与工作知识同图、独立成类(`knowledge/readings/`);
   外部来源新增 `source_type: web`,内部资料仍使用 `source_type: feishu_doc`。见 §0、§3、§4.2。
6. **真相源/派生不变量 + auto-link + 重建一等化** — 显式锁定数据不变量(§1.C);写节点时
   自动从 body 提及的节点 id 连边(auto-link);「重建 INDEX」提为一等操作并补灾难恢复。
   源:gbrain 架构借鉴(reading-gbrain-system-of-record / reading-gbrain-retrieval)。
7. **检索栈:INDEX 路由 + grep 全文 + agent 语义** — INDEX 增 `TL;DR` 列扩大语义面(§6);
   `search` 双路召回(扫 INDEX 做语义召回 + `grep` 做全文召回)再图遍历;**不引入向量库/DB**
   —— 个人库尺度下检索器即当前 agent/模型本身。源:gbrain 混合检索借鉴(reading-gbrain-retrieval)。
8. **群聊增量摄取** — 群聊是持续消息流,同一群反复摄取;`feishu_chat` raw 的 `source_window`
   结束点 = 高水位,在 INDEX「群聊摄取进度」表登记(agent 据此查首次/增量),
   `bin/pull-chat.sh --since-last` 据此自动续拉下一窗口。每窗口一个 event,实体节点跨窗口
   累积更新。见 §3、§4.3、§6、SKILL「群聊摄取补充」。
9. **定期摄取(routine digest)** — 会定期更新的源(滚动周会文档、群聊)经用户确认后,raw 打
   `routine` 标记;INDEX「定期摄取清单」表由此派生(替代旧「待消化」表)。「定期摄取」例程
   逐源增量 re-digest,支持手动触发与 skill-use 到期提醒。见 §3、§6、SKILL「定期摄取」。
10. **第一方观点:思路与视角章节 + 全局 context.md** — 使用者/主管/同事的主观工作思路、想法、
   意图作为第一方输入纳入考量。挂在具体 project/area 上的观点 → 节点新增「思路与视角」章节
   (带日期、带作者、只追加日志,标【主张】/【意图】,§4.6);跨主题的工作底色 → 数据目录顶层
   新增 `context.md`(使用者手维护、每次运行加载为「透镜」,§10)。出处严标、绝不硬化为事实。
11. **person 飞书 id + digest 重点关注** — `person` 新增 frontmatter 字段 `feishu_id`(飞书英文
   id = 企业邮箱前缀,全局唯一),作 person 实体消解主键、消歧同名;同名不同 `feishu_id` =
   不同人,须经用户确认(§2、§4.1、§4.3)。digest 时:结合 `context.md` 重点关注使用者本人 /
   其项目 / 团队 / 关注的人及其指令;命中重大事故 / 指标剧变等需高亮的内容,显著记录进节点
   并在 digest 后主动提醒用户(详见 SKILL「digest」)。
12. **person id 与 `feishu_id` 解耦** — person 节点 id = 稳定 slug(同其它 6 类,姓名关键词
   kebab-case),**一经生成永不改名**;`feishu_id` 仅作 frontmatter 字段(实体消解主键、消歧
   同名)+ INDEX 人员表一列。撤销曾短暂采用的「id ≡ feishu_id」方案 —— 后者在 `feishu_id`
   初始不可知、或永久为 `?` 时,被迫走「临时拼音 slug → 改名级联」,易漏错;解耦后全库再无
   任何 node 需要改名。见 §2、§4.1、§6。
13. **定期摄取到期判断改用状态文件** — 「到期提醒」不再扫 journal 散文找上次运行日期,
   改读数据目录的 `.last-routine-digest`(§1.B)。定期摄取例程每次运行后写当天日期 ——
   **空手而归也写**(「复查过」≠「有新增」);journal 行降为纯审计。见 §1.B、SKILL.md。
14. **日报 / 周报归档快照** — 新增 `reports/daily/` 与 `reports/weekly/`。`daily` / `weekly`
   每次先跑定期摄取,再从 journal / raw / nodes 召回事实生成报告;报告不进入 INDEX,但每条事实
   必须能回溯到节点 / raw / journal。同周期再次生成可覆盖,但保留用户手动备注。见 §11、SKILL.md。

**schema 以本文件为准;后续扩展在此节登记。**

---

## 9. dashboard.md — 工作看板

数据目录顶层文件,与 `INDEX.md` 并列。一个**实时工作视图**,**不是知识节点** —— 回答
"我现在该看什么"。

- **持久存储**(用户状态,只此一处保存):📌 长期关注列表、⚠️ 手动提醒。
- **渲染**(每次刷新重算,不持久依赖):📌 各关注项的当前状态、⚠️ 派生项、📅 今日进展。

结构:

```markdown
# 工作看板 · dashboard
> 最后刷新:<YYYY-MM-DD HH:MM>

## 📌 长期关注
| 关注项 | 绑定节点 | 关注什么 | 当前状态 |
|--------|----------|----------|----------|

## ⚠️ 需要关注
- (派生)<陈旧节点 / 未裁决冲突 …>
- (手动)<用户提醒>

## 📅 今日进展(<YYYY-MM-DD>)
- <当天 journal 渲染>
```

- **📌 关注项**:`绑定节点` 列填知识节点 id(能绑则绑),或留空(自由文本项)。
  `当前状态` 列刷新时从绑定节点拉 TL;DR/状态;自由文本项写"—"。
- **📅 今日进展**:**不独立存储**,刷新时从当天 `journal/` 渲染;用户报告的进展先写入
  journal、再渲染到此。跨天自动重置(journal 即历史归档)。
- **⚠️**:派生项刷新时由轻量新鲜度/冲突扫描得到;手动提醒持久存在文件内。
- 看板是 view —— 每次"看板"触发都重新渲染,**不会过时**。

---

## 10. context.md — 全局工作上下文

数据目录顶层文件,与 `INDEX.md` / `dashboard.md` 并列。**使用者主动维护**的全局工作上下文 ——
每次 skill 运行都加载,作为 digest / search / brief / dashboard 的「透镜」。

- **性质**:真相源、不可派生。skill 在 digest / search 等流程中**只读、绝不自动改写**;
  用户明确要求时由 agent 代为增删改(SKILL 的 `context` 子命令)—— 完全通过对话式 agent
  (Codex、OpenClaw 等)使用本 skill 的用户无法直接编辑文件,**必须靠 agent 代维护**。
- **保持简短**:它是「透镜」不是「档案」—— 只放当前有效的上下文,过期内容使用者自行删除。
  每次运行都加载,过长会吃上下文。
- **用法**:见 SKILL「操作前必读」—— digest 时影响怎么解读、什么值得消化;search / brief 时
  在客观答案旁带出使用者视角与主管方向,并在客观信息与陈述意图冲突时主动提示。
  内容呈现给用户时标为「你的视角 / 主管方向」,**非事实**。
- **与「思路与视角」章节的分工**:`context.md` 是**跨主题**的工作底色;节点的「思路与视角」
  章节(§4.6)是**挂在具体 project/area 上**的观点。

**结构由模板锁定** —— 骨架见 skill 目录的 [`templates/context.md`](templates/context.md):固定四个
章节 `我的当前重点` / `主管方向` / `当前约束` / `背景信息`,每条带日期(`- <YYYY-MM-DD> —— <一句话>`)。
首次使用、或数据目录缺 `context.md` 时,由 skill **整份复制**该模板初始化 —— 统一模板,避免各用户
写出五花八门的格式。各章节无内容则留空;`<!-- 指引 -->` 注释保留(持续引导用户、不渲染)。

## 11. reports/ — 日报 / 周报归档快照

报告文件不是知识节点,不进入 `INDEX.md`,但属于用户可手改的真相源快照。它们用于归档某天 /
某周的工作总结,回答"这段时间发生了什么重要事,与我和团队有什么关系,后续该看什么"。

目录与命名:

```text
reports/
  daily/
    2026-05-25.md
  weekly/
    2026-W22.md
```

- **生成来源**:范围内 `journal/`、`raw_data/` frontmatter、`knowledge/` 节点及其 links。
- **模板**:skill 目录 `templates/report-daily.md`、`templates/report-weekly.md`。
- **覆盖规则**:同一日期 / 周再次生成可覆盖报告正文;若旧报告有 `## 手动补充 / 备注`,必须保留该章节内容。
- **排序**:章节内带时间条目按事件发生时间倒序;时间不明放末尾并标注。
- **溯源**:每个事实性条目带节点 id、raw_id 或 journal 日期;无来源不写事实结论。
- **git**:报告写入后按写入规范在知识库数据目录本地 git 创建回滚点,永不 push。
