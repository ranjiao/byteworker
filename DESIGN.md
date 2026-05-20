# byteworker 知识库 — 存储结构与字段设计

> 本文档锁定「存什么、存成什么格式、字段怎么设计」。SKILL.md 与 templates/ 按此实现。
> 来源:CEO 评审 2026-05-20(SCOPE EXPANSION 模式);2026-05-20 改为实体图模型。

---

## 0. 核心模型:实体图

知识库是一张**实体图**。不再用会漂移的「topic 分类」,而用 **6 类实体/记录节点**,
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

**图的边:** event 链接它涉及的 person/project/org;decision 链接相关 project/event 与决策人;
project 链接成员 person、所属 area、所属 org。查询「关于张三我都知道什么」=
person-zhang-san 节点 + 所有链回他的 event/decision/project。

---

## 1. 目录职责 — 逻辑与数据严格分离

byteworker 由**两个物理隔离**的部分组成。

### A. skill 仓库(纯 agent 逻辑,可进 git / GitHub)

| 文件/目录 | 存什么 |
|-----------|--------|
| `SKILL.md` | skill 行为定义(摄取/查询/更新/新鲜度/简报/help) |
| `DESIGN.md` | 本文档:存储 schema |
| `templates/` | 6 类节点骨架模板 |
| `TODOS.md` / `CLAUDE.md` | 延后项 / 仓库须知 |
| `.kbconfig` | 知识库数据目录的绝对路径(**已 gitignore,不提交**) |

### B. 知识库数据目录(业务数据,用户指定,**绝不进 skill 仓库的 git**)

| 目录/文件 | 存什么 | 谁写 | 可变性 |
|-----------|--------|------|--------|
| `raw_data/` | 摄取的**逐字原文** + 溯源元数据,一次摄取一文件 | skill 写入,**永不改写** | 只增不改 |
| `knowledge/{people,projects,areas,orgs,events,decisions}/` | 6 类节点笔记,按类型分子目录(固定 6 个,不漂移) | skill 写入/更新 | 实体可更新;记录定型 |
| `journal/` | 摄取/更新/扫描事件的**时间线日志** | skill 追加 | 只追加 |
| `INDEX.md` | 主索引:6 类节点登记表 + 待消化表 | skill 维护,可全量重建 | 高频更新 |

数据目录路径由用户首次使用时指定,记于 skill 仓库的 `.kbconfig`(已 gitignore)。
数据目录含**公司机密内容**,绝不外传、绝不纳入 skill 仓库的 git。

**核心原则:raw_data/ 是不可变真相源,knowledge/ 是可变消化产物。** 节点出错永远可回
对应 raw_data 重新消化,两者通过 id 双向引用。

---

## 2. 命名规范

- **slug**:取标题核心关键词 → 英文/拼音 kebab-case,≤40 字符;碰撞追加 `-2`/`-3`。
- **raw 文件**:`raw_data/<YYYY-MM-DD>-<slug>.md`,`raw_id` = `raw-<YYYY-MM-DD>-<slug>`。
- **节点文件 / id**:
  - 实体:`knowledge/<类型复数>/<前缀><slug>.md`,如 `person-zhang-san`、`project-q2-roadmap`、`area-rec-system`、`org-data-platform-team`。
  - 事件含日期:`event-<YYYY-MM-DD>-<slug>`,如 `event-2026-05-20-q2-review`。
  - 决策:`decision-<slug>`。
- **journal**:`journal/<YYYY-MM>/<YYYY-MM-DD>.md`。
- 单类节点 > 200 时再分子目录(TODOS)。

---

## 3. raw_data/ — 原始输入

每次摄取写一个文件,逐字保留,**不做任何改写/删减**。

```markdown
---
raw_id: raw-2026-05-20-q2-roadmap-review
ingested: 2026-05-20T14:30:00+08:00
source_type: feishu_doc | feishu_minutes | feishu_meeting | local_md
source_url: https://<feishu-url>           # 本地 md 则填原路径
source_title: Q2 路线图评审会
digest_status: pending | digested | failed
digest_targets:                            # 本次摄取触达的所有节点 id
  - event-2026-05-20-q2-review
  - decision-q2-scope
  - project-q2-roadmap
---

# Q2 路线图评审会

<逐字原文 / lark-minutes 纪要+逐字稿 / lark-doc 文档正文,原样粘贴>
```

---

## 4. knowledge/ — 节点笔记

### 4.1 通用 frontmatter(6 类都有)

```yaml
---
id: project-q2-roadmap
title: Q2 产品路线图
type: person | project | area | org | event | decision
tags: [roadmap, q2]
status: current | stale | superseded         # 实体常为 current/stale;记录可 superseded
created: 2026-05-20
updated: 2026-05-20
last_verified: 2026-05-20                     # 新鲜度扫描依据
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
| `type` | ✓ | 6 类之一,决定子目录与 body 结构 |
| `tags` | ✓ | 自由二级标签,承载角色特异性(数据集名、渠道、技术栈…);优先复用已有 tag |
| `status` | ✓ | `current` / `stale` 疑似过期 / `superseded` 已被取代 |
| `created`/`updated`/`last_verified` | ✓ | 创建 / 最后修改 / 最后被新输入或人工确认的日期 |
| `superseded_by` | ✗ | 退役时指向取代它的节点 |
| `sources` | ✓ | 溯源根,指回 raw_data 或飞书链接 |
| `links` | ✗ | 关联节点 id,**双向维护**;id 前缀即对端类型 |

> 不再有 `topic` 字段——领域结构由 `area`/`org` 节点 + `links` 承载,topic 治理问题消解。

### 4.2 body 结构(按 type)

所有类型 body 首行统一 TL;DR(查询先返回它):
```markdown
# <title>

> **TL;DR:** <一句话摘要>
```

**`person`(实体)**
```markdown
## 基本信息        <!-- 角色 / 所属团队 / 对接方式 -->
## 负责什么
## 协作历史与关键交互
## 偏好 / 风格 / 注意点
## 关联节点
```

**`project`(实体,广义专项/事项)**
```markdown
## 当前状态
## 目标 / 范围
## 关键里程碑 / 时间线
## 成员 / 相关方     <!-- person 链接 -->
## 风险 / 阻塞
## 关联决策与事件
## 历史              <!-- 被新输入推翻的旧状态移入,标来源+日期 -->
```

**`area`(实体,主题领域常青知识)**
```markdown
## 概述 / 定义
## 关键知识点
## 规范 / 流程 / how-to
## 踩坑 / 注意事项
## 相关节点与外部链接
```

**`org`(实体,组织/团队/供应商)**
```markdown
## 基本信息        <!-- 内部团队 / 外部供应商;职责 -->
## 关键成员         <!-- person 链接 -->
## 对接方式 / 流程
## 协作历史
## 关联项目
```

**`event`(记录,产生即定型)**
```markdown
## 事件信息        <!-- 时间 / 类型:会议|评审|发布 / 参会人 -->
## 议程与讨论
## 结论
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
## 历史
```

### 4.3 一次摄取的产出(digest 扇出)

以一次会议为例,raw → 多节点:
1. **必产 1 个 `event`**:这次会议的 digest 快照。
2. **抽取 N 个 `decision`**:会上每个明确决策抽成独立节点。
3. **创建或更新实体节点**:会上实质涉及的 person/project/org/area —— 不存在则建,
   已存在则把新信息累积进对应章节(状态/协作历史等)。
4. **全部互链** `links`,并登记进 raw 的 `digest_targets`。

**实体消解:** 创建实体前先按标题/名字在 INDEX 比对,命中已有节点则更新而非新建;
有歧义则高亮问用户(避免同一个人产生两个 person 节点)。

### 4.4 什么该进知识库

**该存:** 决策与理由、项目/事项状态、常青参考知识、会议结论与待办、协作关系。
**不该存:** 一周后即失效且无留存价值的琐碎、纯寒暄。
边界不清则 agent 高亮问用户,不静默丢弃也不硬塞。

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
- 16:00 新鲜度扫描:3 条 stale → project-old-x, person-li-si, area-legacy-flow
```

每行含:时刻、动作、输入源、触达节点 id、raw_id、是否冲突。这是审计日志。

---

## 6. INDEX.md — 主索引

skill 自动维护,可从全部 frontmatter 全量重建。按 6 类分节,一行一节点:

```markdown
# 知识库索引

## 人员 (person)
| id | 标题 | status | last_verified |
|----|------|--------|----------------|

## 项目 (project)
| id | 标题 | status | last_verified |

## 主题领域 (area) / 组织 (org) / 事件 (event) / 决策 (decision)
| …同上… |

## 待消化 (raw digest_status=pending)
| raw_id | source_type | 标题 | ingested |
```

- 查询先扫 INDEX 再定向 Read 节点;写入时**增量更新**对应行,不每次全扫。
- 一致性兜底:某类 `knowledge/<类型>/` 文件数 ≠ INDEX 该节行数 → 触发全量重建。
  (纯内容编辑不改行数,无法靠计数发现 → 故增量更新是主路径。)
- 单类节点行数 > 200 → skill 必须提示该类按子目录分片(TODOS)。

---

## 7. templates/ — 节点骨架

```
templates/
  README.md            模板使用说明
  node-person.md       \
  node-project.md       \  各 = §4.1 通用 frontmatter
  node-area.md           > + §4.2 对应 type 的 body 章节
  node-org.md           /   + 章节内 <!-- 指引 --> 注释(填什么、从哪提取)
  node-event.md        /
  node-decision.md
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
   (`knowledge/`、`raw_data/`、`journal/`、`INDEX.md`)存在用户指定的独立目录,
   **绝不进 skill 仓库的 git**。数据目录路径记于 `.kbconfig`(gitignore)。

**schema 至此完全冻结。**
