# Byteworker 开发文档

本目录面向 Byteworker 维护者和 coding agent，不承载用户知识库业务数据。

- [`ARCHITECTURE.md`](ARCHITECTURE.md)：系统流程、模块职责、依赖方向、失败边界与架构治理。
- [`DESIGN.md`](DESIGN.md)：持久化目录、schema、数据不变量与兼容约束。
- [`PROACTIVE_INFORMATION_PROCESSING_DESIGN.md`](PROACTIVE_INFORMATION_PROCESSING_DESIGN.md)：
  Dreaming / 主动信息处理的设计与实施记录。
- [`TODOS.md`](TODOS.md)：明确延后的工程事项。

运行时 Agent 的行为入口仍是仓库根目录的 [`SKILL.md`](../../SKILL.md)；按场景加载的执行细则位于
[`references/`](../../references/)。
