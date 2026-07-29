# byteworker skill —— 仓库须知

本仓库**只包含 agent 逻辑**:`SKILL.md`、`ARCHITECTURE.md`、`DESIGN.md`、`templates/`、
`references/`(SKILL.md 按需加载的细则)、`bin/`、`lib/`、`tests/`、`viewer/`、`INSTALL.md`、
`TUTORIAL.md`。

## 铁律

- **业务数据绝不进本仓库。** `knowledge/`、`raw_data/`、`provenance/`、`journal/`、`INDEX.md` 及任何
  节点 md 一律不提交 —— `.gitignore` 已拦截,你也必须主动遵守。
- 知识库数据存在用户指定的**独立目录**(路径见 `.kbconfig`,已 gitignore),含公司机密,
  绝不外传、绝不进本仓库。
- 改 skill 行为 → 改 `SKILL.md`(深层 digest 细则在 `references/digest-*.md`,SKILL.md 按场景路由过去);
  改存储 schema → 改 `DESIGN.md`。

## 架构治理（代码与流程变更必读）

- 修改 `bin/`、`lib/` 或信息处理主流程前，先读 [`ARCHITECTURE.md`](ARCHITECTURE.md) 的相关章节；
  不允许只凭局部代码继续堆叠分支。
- 模块、职责、依赖方向、跨层契约、信息流、失败边界或成功判定发生变化时，必须在**同一变更**
  中同步 `ARCHITECTURE.md`。不能用“后续补文档”交付已知漂移。
- schema 或知识库目录变化时还要同步 `DESIGN.md`；Agent 行为变化时还要同步 `SKILL.md`
  和对应 `references/`。
- 新 provider 的差异留在 adapter / compatibility 层，不得把 provider 分支重新带入
  `lib/digest_txn.py` 或 `lib/kb_query.py`。
- 交付前至少运行架构契约测试、受影响模块测试、全量测试、shell 语法检查和
  `.coveragerc` 定义的覆盖率门禁，并在交付说明中写明架构是否变化、文档如何同步、
  是否留下兼容层或技术债务。

## 这是什么

个人飞书工作知识库 skill。摄取飞书文档/会议纪要/md → 消化成实体图笔记 → 对话式查询。
用法见 `SKILL.md`(或对 skill 说 `help`)。

本 skill 每周静默自动从 GitHub fast-forward 更新(`bin/update-check.sh`,由 SKILL.md「操作前必读」触发)。
