# templates/ — 节点骨架模板

byteworker 知识库每类节点的骨架。digest 时 agent 按对应模板生成节点笔记。

| 模板 | 用于 | 节点性质 |
|------|------|----------|
| `node-person.md` | 人员 | 实体 · 持续更新 |
| `node-project.md` | 项目 / 专项 / 有生命周期的持续事项 | 实体 · 持续更新 |
| `node-area.md` | 主题领域 | 实体 · 持续更新 |
| `node-org.md` | 组织 / 团队 / 供应商 | 实体 · 持续更新 |
| `node-event.md` | 会议 / 评审 / 发布 | 记录 · 产生即定型 |
| `node-decision.md` | 决策 | 记录 · 可被取代 |

## 用法

1. 复制对应模板。
2. 填 frontmatter(字段定义见 `DESIGN.md` §4.1;命名规范见 §2)。
3. 按 body 里的 `<!-- 指引 -->` 注释填写各章节。
4. 生成正式节点时**删除所有指引注释**。
5. 无法判定类型:实体类倾向 `node-area`,记录类倾向 `node-event`,并在 journal 标注。

schema 以 `DESIGN.md` 为唯一真相源。
