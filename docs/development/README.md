# Byteworker 开发文档

本目录面向维护者和 coding agent。运行时 Agent 的入口仍是仓库根目录的
[`SKILL.md`](../../SKILL.md)，按场景加载的执行细则位于 [`references/`](../../references/)；
不要把 `docs/development/` 当成运行时手册整包加载。

## 按任务选择入口

| 要做什么 | 先读什么 | 这里定义什么 |
|---|---|---|
| 修改系统流程、模块职责、依赖或失败边界 | [`ARCHITECTURE.md`](ARCHITECTURE.md) | 当前实现架构 |
| 修改知识库目录、持久化 schema 或数据不变量 | [`DESIGN.md`](DESIGN.md) | 当前存储契约 |
| 判断文档该放哪里、谁是权威 | [`governance/documentation.md`](governance/documentation.md) | 文档治理规则 |
| 查找仍待实施的工作 | [`plans/`](plans/) | 活跃计划与 backlog |
| 查找一次审查或性能测量 | [`evidence/`](evidence/) | 带日期的非规范性证据 |
| 追溯已完成计划或旧设计 | [`archive/`](archive/) | 只用于历史追踪 |
| 查找架构决策及其理由 | [`decisions/`](decisions/) | 决策索引与后续 ADR |

[`catalog.json`](catalog.json) 是机器可读目录。新增、移动或删除开发文档时必须在同一变更中更新它，
并运行 `tests/test_documentation_catalog.py`。

## 当前约束

`ARCHITECTURE.md` 和 `DESIGN.md` 暂时保留稳定路径，避免破坏既有入口。两者是历史形成的长文档；
后续修改涉及某个独立模块时，应优先把该模块拆成有明确 owner 的现状文档，再从稳定入口链接，
而不是继续追加新的实施记录、review 或 benchmark。
