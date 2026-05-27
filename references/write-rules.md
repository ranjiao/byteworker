# byteworker · 写入规范

> 由 `SKILL.md` 路由到这里。凡要写 `raw_data/`、`knowledge/`、`reports/`、`dashboard.md`、
> `INDEX.md`、`journal/` 或 `context.md`,动手前必须遵守本文件。

## 通用规则

- 节点文件按 `templates/node-<type>.md` 骨架;生成时**删除** `<!-- 指引 -->` 注释。
- **原子写入**:先写 `<file>.tmp` → 校验 frontmatter 完整 → move 覆盖,避免半成品。
- **双向 links**:写 A→B 链接,必同时在 B 的 `links` 写回 A。
- **自动连边(auto-link)**:写节点 body 时扫描正文,凡出现其它节点 id(形如 `person-xxx`、`project-xxx` 等 7 类前缀)且该 id 在 INDEX 中确实存在的,自动并入本节点 `links` 并双向写回 —— 不依赖 digest 时主动想起,避免漏连。
- **INDEX 增量更新**:写/改节点后更新 `INDEX.md` 对应行,不每次全扫。若发现某类 `knowledge/<type>/` 文件数 ≠ INDEX 该节行数 → 全量重建(见 `references/maintenance.md`)。
- **journal**:每次摄取/更新/看板/日报/周报写操作后,向 `journal/<YYYY-MM>/<YYYY-MM-DD>.md` 追加一行 —— 时刻、动作、触达节点 id、raw_id、报告路径、是否冲突。
- **回滚点**:每次写操作完成后,在知识库数据目录执行 `git add -A && git commit`(该目录自身的本地 git,**永不 push**),使每一步可回滚。
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
