# Byteworker 开发文档

维护者和 coding agent 按下表查找开发文档。运行时从 [`SKILL.md`](../../SKILL.md) 进入，
按场景加载 [`references/`](../../references/) 的规则。

## 按任务选择入口

| 要做什么 | 先读什么 |
|---|---|
| 修改系统流程、模块职责、依赖或失败边界 | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| 修改知识库目录、持久化 schema 或数据不变量 | [`DESIGN.md`](DESIGN.md) |
| 确定文档放在哪里、以哪份为准 | [`governance/documentation.md`](governance/documentation.md) |
| 查看待实施的工作 | [`plans/`](plans/) |
| 查找审查或性能测量 | [`evidence/`](evidence/) |
| 追溯已完成的计划或旧设计 | [`archive/`](archive/) |
| 查找架构决策及其理由 | [`decisions/`](decisions/) |

[`catalog.json`](catalog.json) 是机器可读目录。新增、移动或删除开发文档时必须在同一变更中更新它，
并运行 `tests/test_documentation_catalog.py`。

## 当前约束

`ARCHITECTURE.md` 和 `DESIGN.md` 保留现有路径。修改独立模块时，优先拆出有明确维护者的模块文档，
再从原入口链接。实施记录、review 和 benchmark 放到各自的目录。

本地检查见 [验证指南](governance/validation.md)。2026 年 11 月的审查范围见 [完成记录](archive/initiatives/2026-11-review-followups.md)。
