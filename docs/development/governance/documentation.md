# 开发文档治理

先按读者选入口，再按内容的状态决定放在哪个目录。

## 1. 边界

| 位置 | 责任 | 不应承载 |
|---|---|---|
| 根目录 `README.md` | 给使用者介绍能做什么、为什么好用、怎么开始 | 实现原理、完整命令和内部流程 |
| `INSTALL.md`、`TUTORIAL.md` | 安装和首次使用，兼顾用户阅读与 Agent 执行 | 重复的运行时规则 |
| `SKILL.md` | 运行时 Agent 行为入口和场景路由 | 实现细节、历史记录 |
| `references/` | Agent 按场景加载的步骤、约束和机器协议。`help.md` 直接展示给用户 | 架构演进日志 |
| `templates/` | 输出与持久化产物骨架 | 工作流说明 |
| `bin/README.md` | 维护者和 Agent 查命令、参数和写入边界 | 面向用户的功能介绍 |
| `docs/development/` | 维护者所需的架构、schema、决策、计划、证据和历史 | 用户知识库数据、运行时整包上下文 |

## 2. 一项事实只有一个权威

- `ARCHITECTURE.md` 定义当前系统流程、模块职责、依赖方向、跨层契约和失败边界。
- `DESIGN.md` 定义当前知识库目录、持久化 schema 和数据不变量。
- `decisions/` 记录为什么选择某个长期约束、替代方案和后果，不复制完整现状契约。
- `plans/` 只记录尚未完成、仍准备实施的工作，不把完成清单长期留在活跃入口。
- `evidence/` 保存带日期的 review、benchmark 和验证快照。证据支持决策，但不覆盖当前架构。
- `archive/` 保存已完成、被替代或仅具历史价值的材料，不是实现依据。

用代码和测试核对实现，用上面的规范判断预期行为。两者有出入时，在同一变更中修正。
review、计划和归档材料只供追溯和参考。

## 3. 生命周期

| 状态 | 允许位置 | 迁移规则 |
|---|---|---|
| `current` | 稳定入口、治理、索引 | 内容失效时原地更新或由明确替代文档接管 |
| `active` | `plans/` | 完成或取消时移入 `archive/initiatives/` |
| `point_in_time` | `evidence/` | 新测量创建新的日期文件，不静默改写旧结论 |
| `completed` | `archive/initiatives/` | 只做事实、链接和格式修正 |
| `historical` | `archive/designs/` | 只用于追溯；旧待确认项不会自动恢复成 active |

计划完成、取消或被替代时，在同一变更中移动文档。归档中尚未解决的问题需重新确认，再写入 `plans/`。

## 4. 命名与规模

- 当前规范使用稳定、描述性的文件名，例如 `ARCHITECTURE.md` 和 `DESIGN.md`。
- review、benchmark、完成计划和历史设计使用 `YYYY-MM-主题.md`，日期表示材料基线月份。
- 目录 `README.md` 只做路由，不复制子文档正文，建议控制在 150 行内。
- 新的模块级现状文档建议控制在 700 行内；超过时应按职责或读者任务拆分。
- `ARCHITECTURE.md` 和 `DESIGN.md` 是已有的长文档。以后修改独立模块时优先拆出模块文档。
  专项设计、执行清单和测量结果放到各自目录。

## 5. 变更规则

1. 新增文档前先判断它是当前规范、决策、活跃计划、时间点证据还是历史材料。
2. 新增、移动、改名或删除 Markdown 文档时，同步更新 `catalog.json` 和所属目录的 `README.md`。
3. 架构变化同步 `ARCHITECTURE.md`；schema 或知识库目录变化同步 `DESIGN.md`。
4. Agent 行为变化同步 `SKILL.md` 和对应 `references/`，不要只写开发文档。
5. review 和 plan 中的建议在落地后写回当前权威文档；历史材料保留当时基线，不冒充现状。
6. 外部或运行时入口不得依赖 `archive/` 中的内容。

## 6. 自动门禁

`tests/test_documentation_catalog.py` 检查：

- catalog schema、ID 和路径唯一；
- 所有开发 Markdown 均被登记，没有孤儿文档；
- 顶层只保留稳定入口，生命周期与目录一致；
- 相对 Markdown 链接有效，代码围栏闭合；
- `SKILL.md` 和 `references/` 不把归档材料作为运行时依赖。

交付前还要检查文档内容，并运行受影响测试、全量测试和 `git diff --check`。
