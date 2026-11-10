# Byteworker 模板

这里存放知识节点、工作上下文、摄取计划和报告的模板。字段以 `docs/development/DESIGN.md` 为准。

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
| `node-thinking.md` | 用户自己的认知、直觉、假设、推演和方案 | 持续更新 · 自然语言优先 |

digest 负责生成来源节点。只有用户明确要求保存或更新思考时，才使用 `thinking` 模板。

1. 复制对应模板。
2. 填写 frontmatter。字段和命名规范见 `docs/development/DESIGN.md` §4.1 和 §2。
3. 按正文里的 `<!-- 指引 -->` 填写，生成正式节点时删除所有指引注释。
4. 无法判定类型时，按 `references/semantic-policy.md` 给出有限候选和证据，等用户确认后再写入。

## context.md 模板

新建知识库或发现顶层缺少 `context.md` 时，整份复制模板，包括 `<!-- 指引 -->` 注释。
之后由用户通过对话维护工作上下文。字段见 `docs/development/DESIGN.md` §10。

## digest plan 模板

这些模板用于查字段。新增单来源时，adapter 按 `source-bundle-v2.json` 生成 bundle，计划用
`digest-plan-v2.json` 引用 bundle。多来源需要一起成功、一起回滚或共同更新节点时，用
`digest-batch-plan-v2.json`，每个 input 只引用已校验的 SourceBundle。v2 计划禁止复制 source 或 anchors。
v1 模板只供旧调用兼容。

在系统临时目录填写 Bundle 路径、raw 目标、完整候选节点、逐条事实的 evidence 映射、journal 摘要和
commit message。再按 `references/digest-transaction.md` 调用 `bin/digest-txn.py`。
含业务数据的 plan 和候选节点禁止放进模板目录或 skill 仓库。

## report 模板

| 模板 | 用于 | 输出位置 |
|------|------|----------|
| `report-daily.md` | 自动日报 / 自然语言补跑 | `reports/daily/<YYYY-MM-DD>.md` |
| `report-weekly.md` | 自动周报 / 自然语言补跑 | `reports/weekly/<YYYY>-W<WW>.md` |
| `report-morning.md` | Dreaming 晨报 | `reports/morning/<YYYY-MM-DD>.md` |
| `report-template.html` | Dreaming 面向用户的 HTML 模板 | `state/dreaming/reports/<kind>-<period>/artifacts/report.html` |
| `report-template.md` | Dreaming HTML 模板渲染契约 | 设计 / 开发参考，不进入报告产物 |
| `report-automation-daily.md` | 宿主本地自动日报 prompt | harness 定时任务配置 |
| `report-automation-weekly.md` | 宿主本地自动周报 prompt | harness 定时任务配置 |
| `report-automation-recovery.md` | 自动报告缺口检查与单期补跑 prompt | harness 周期性补偿任务配置 |

生成报告时，在知识库目录填入节点、raw、journal 或已提交的 Dreaming Finding 中的事实。
保留 `## 手动补充 / 备注`，供用户编辑。

HTML 模板必须是独立文件，禁止加载外部 JS、CSS、字体或图片。Python 渲染器转义业务文本后替换
`{{PLACEHOLDER}}`。Dreaming 只读取 `report-template.html`，缺少此文件时渲染失败。
