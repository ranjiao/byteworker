# byteworker · 写入规范

> 由 `SKILL.md` 路由到这里。凡要写 `raw_data/`、`knowledge/`、`reports/`、`dashboard.md`、
> `INDEX.md`、`journal/` 或 `context.md`,动手前必须遵守本文件。

## 通用规则

- 节点文件按 `templates/node-<type>.md` 骨架;生成时**删除** `<!-- 指引 -->` 注释。
- **原子写入**:先写 `<file>.tmp` → 校验 frontmatter 完整 → move 覆盖,避免半成品。
- **双向 links**:写 A→B 链接,必同时在 B 的 `links` 写回 A。
- **sources / links 去重**:更新已有节点时,`sources` 与 `links` 必须去重保序;同一个
  `raw_id`、同一个 URL、同一个节点 id 不重复追加。若同源新版本产生新的 `raw_id`,可以追加新
  `raw_id`,但不要重复追加旧来源。
- **自动连边(auto-link)**:写节点 body 时扫描正文,凡出现其它节点 id(形如 `person-xxx`、`project-xxx` 等 7 类前缀)且该 id 在 INDEX 中确实存在的,自动并入本节点 `links` 并双向写回 —— 不依赖 digest 时主动想起,避免漏连。批量修复时运行 `bin/repair-links.sh --autolink`。
- **INDEX 增量更新**:写/改节点后更新 `INDEX.md` 对应行,不每次全扫。若发现某类 `knowledge/<type>/` 文件数 ≠ INDEX 该节行数 → 全量重建(见 `references/maintenance.md`)。
- **journal**:每次摄取/更新/看板/日报/周报写操作后,向 `journal/<YYYY-MM>/<YYYY-MM-DD>.md` 追加一行 —— 时刻、动作、触达节点 id、raw_id、报告路径、是否冲突。
- **回滚点**:每次写操作完成后,在知识库数据目录只暂存本次实际改动的路径(例如 `git add raw_data/<file> knowledge/projects/<file> INDEX.md journal/<date>.md`),再 `git commit`(该目录自身的本地 git,**永不 push**),使每一步可回滚。不要用 `git add -A` 把无关手改一起卷入。
- **命名 / 字段**:严格按 DESIGN.md §2(命名)与 §4.1(字段)。
- 单类节点 > 200 条 → 提示用户该类按子目录分片(暂不自动做)。

## 时间格式

严格按 DESIGN.md §2.1。写入任何 raw frontmatter、knowledge 节点、INDEX、journal、dashboard、reports 前,先把可结构化时间规范化:

- 日期写 `YYYY-MM-DD`。
- 人读时间写 `YYYY-MM-DD HH:MM`。
- 机器边界 / 群聊高水位写 `YYYY-MM-DDTHH:MM:SS+08:00`。
- ISO 周写 `YYYY-Www`。
- 不要在 skill 生成内容中写 `20260520`、`5-21`、`05/21` 等裸格式;这些只允许保留在 raw 原文正文里。

## 时间顺序

节点 body 中凡是带日期 / 时间的条目,统一按**事件发生时间倒序**排列(最新发生的在前),而不是按写入时间随手追加。

典型章节:
- `person` 的「协作历史与关键交互」。
- `project` 的「关联文档与会议 / 关键进展 / 思路与视角 / 历史」。
- `org` 的「协作历史」。
- `decision` 的「历史」。

新增条目时插入到正确时间位置;日期不明的条目放在该章节末尾并标注时间不明。

## 章节条目去重

更新已有节点时,先读原章节,再按下面的 key 去重;命中重复则跳过或合并来源,不得把同一事实
反复追加成多条。

- `project`「关联文档与会议」:同日期 / 周期 + 同 `raw_id` / `event` / `reading` / URL 视为同一条。
- `project`「关键进展」:同日期 + 同来源 + 语义相同视为同一条;同源新版本只在事实发生变化时改写
  对应条目,旧事实被推翻时移入「历史」。
- `project` / `area`「思路与视角」:同日期 + 同作者 + 同来源 + 同【主张】/【意图】内容视为同一条。
- `person`「协作历史与关键交互」:同日期 + 同 `event` / `raw_id` / `reading` 视为同一条。
- `org`「协作历史」:同日期 + 同来源节点 / `raw_id` 视为同一条。
- `decision`「历史」:同来源 + 同状态变化视为同一条;同一决策新版本改变结论时,用
  `status: superseded` / `superseded_by` 或「历史」记录演进,不要制造同义重复 decision。

条目去重不等于丢信息:若新输入补充了同一条目的关键细节,合并到原条目并追加新 `source`;若两条
事实相互矛盾,按 digest 的冲突检测交给用户裁决。
