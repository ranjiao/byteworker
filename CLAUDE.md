# byteworker skill —— 仓库须知

本仓库**只包含 agent 逻辑**:`SKILL.md`、`DESIGN.md`、`templates/`。

## 铁律

- **业务数据绝不进本仓库。** `knowledge/`、`raw_data/`、`journal/`、`INDEX.md` 及任何
  节点 md 一律不提交 —— `.gitignore` 已拦截,你也必须主动遵守。
- 知识库数据存在用户指定的**独立目录**(路径见 `.kbconfig`,已 gitignore),含公司机密,
  绝不外传、绝不进本仓库。
- 改 skill 行为 → 改 `SKILL.md`;改存储 schema → 改 `DESIGN.md`。

## 这是什么

个人飞书工作知识库 skill。摄取飞书文档/会议纪要/md → 消化成实体图笔记 → 对话式查询。
用法见 `SKILL.md`(或对 skill 说 `help`)。
