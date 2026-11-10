# Byteworker 仓库须知

本仓库只保存通用代码、文档、测试和模板。用户用法见 [README.md](README.md)，Agent 入口见 [SKILL.md](SKILL.md)。

## 业务数据

- 知识库保存在用户指定的独立目录，路径记在已被 Git 忽略的 `.kbconfig` 中。
- 禁止外传业务数据，也禁止把 `knowledge/`、`raw_data/`、`provenance/`、`journal/`、`INDEX.md`
  和任何节点 Markdown 放入本仓库或提交到 Git。`.gitignore` 提供额外保护。

## 架构治理（代码与流程变更必读）

- 修改 `bin/`、`lib/` 或信息处理主流程前，先读 [`docs/development/ARCHITECTURE.md`](docs/development/ARCHITECTURE.md)
  的相关章节，确认新逻辑应放在哪一层。
- 模块、职责、依赖方向、跨层契约、信息流、失败边界或成功判定变化时，在**同一变更**中同步架构文档。
- schema 或知识库目录变化时，同步 `docs/development/DESIGN.md`。Agent 行为变化时，同步 `SKILL.md` 和对应
  `references/`。digest 的细则放在 `references/digest-*.md`，由 `SKILL.md` 按场景加载。
- 新 provider 的差异留在 adapter / compatibility 层，不得把 provider 分支重新带入
  `lib/digest_txn.py` 或 `lib/kb_query.py`。
- 交付前至少运行架构契约测试、受影响模块测试、全量测试、shell 语法检查和
  `.coveragerc` 定义的覆盖率门禁，并在交付说明中写明架构是否变化、文档如何同步、
  是否留下兼容层或技术债务。

会话预检通过 `bin/update-check.sh` 从 GitHub 检查快进更新。成功检查后七天内静默跳过重复检查。
