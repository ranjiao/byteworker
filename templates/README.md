# templates/ — 骨架模板

byteworker 用到的骨架。`node-*.md` 是 7 类知识节点的骨架,digest 时 agent 按对应模板生成
节点笔记;`context.md` 是数据目录顶层「全局工作上下文」文件的骨架;`report-*.md` 是报告输出骨架。

## 节点模板

| 模板 | 用于 | 节点性质 |
|------|------|----------|
| `node-person.md` | 人员 | 实体 · 持续更新 |
| `node-project.md` | 项目 / 专项 / 有生命周期的持续事项 | 实体 · 持续更新 |
| `node-area.md` | 主题领域 | 实体 · 持续更新 |
| `node-org.md` | 组织 / 团队 / 供应商 | 实体 · 持续更新 |
| `node-event.md` | 会议 / 评审 / 发布 | 记录 · 产生即定型 |
| `node-decision.md` | 决策 | 记录 · 可被取代 |
| `node-reading.md` | 外部 blog / 论文 / wiki 读物;内部路线思考 / 方法论 / 调研 / 技术白皮书资料卡 | 记录 · 低维护 |

用法:

1. 复制对应模板。
2. 填 frontmatter(字段定义见 `DESIGN.md` §4.1;命名规范见 §2)。
3. 按 body 里的 `<!-- 指引 -->` 注释填写各章节。
4. 生成正式节点时**删除所有指引注释**。
5. 无法判定类型:实体类倾向 `node-area`,记录类倾向 `node-event`,并在 journal 标注。

## context.md 模板

`context.md` —— 数据目录顶层「全局工作上下文」文件的骨架(见 `DESIGN.md` §10)。

**与节点模板不同**:它是整文件骨架 —— 首次使用、或数据目录缺 `context.md` 时,由 skill
**整份复制**为初始 `context.md`,之后由用户**手维护**。它的 `<!-- 指引 -->` 注释**保留不删**
(持续引导用户按统一格式填写)。统一模板 = 避免不同用户写出五花八门的格式。

schema 以 `DESIGN.md` 为唯一真相源。

## report 模板

| 模板 | 用于 | 输出位置 |
|------|------|----------|
| `report-daily.md` | `/byteworker daily` 日报 | `reports/daily/<YYYY-MM-DD>.md` |
| `report-weekly.md` | `/byteworker weekly` 周报 | `reports/weekly/<YYYY>-W<WW>.md` |
| `report-im.md` | `/byteworker inbox` IM 摘要 | `reports/im/<YYYY-MM-DD>.md` 或 `reports/im/<start>__<end>.md` |

报告模板只定义结构,不含业务数据。生成报告时复制结构到知识库数据目录,填入从节点 / raw /
journal 或 IM Inbox 候选 threads 精判得到的事实,并保留 `## 手动补充 / 备注` 供用户自行改写。
