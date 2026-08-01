# Byteworker 架构与实现审计

日期：2026-07-31
范围：仓库内全部受版本控制的代码、测试、架构文档、Skill 路由与 references
基线：`f4577ea fix: persist resolved runtime paths`

## 1. 结论摘要

当前实现已经形成较清晰的五层结构，SourceBundle、DigestPlan、provenance、SnapshotStore、
provider-neutral query 等核心契约也有较完整的测试保护。全量 388 项测试通过，branch coverage
为 77.8%，超过仓库 75% 门禁。事务代码对单流程内的原子写入、Git 暂存区隔离、remote 禁止和
失败恢复投入较多，主体方向正确。

本次审计仍确认 5 个需要处理的核心问题：

| 优先级 | 问题 | 主要影响 |
|---|---|---|
| P1 | 多个 KB 写入流程使用不同锁 | 并发时可能混入提交、覆盖 INDEX/journal 或破坏回滚 |
| P1 | update postflight 失败路径不具备事务原子性 | 失败 repair 仍可能提交；提交失败会遗留 staged/dirty 状态 |
| P1 | URL 凭据识别可绕过 | userinfo、`tenant_access_token` 等可进入 Profile/Bundle 和本地 Git |
| P2 | preflight 在自更新后继续混用旧模块与新脚本 | 单次调用可能运行不兼容版本组合 |
| P2 | Profile provider validator 反向依赖生命周期模块 | 与文档依赖方向不符，并形成真实 import cycle |

最优先的结构性改进是引入一个 KB 仓库级写事务协调器，让所有会修改工作树、Git index 或
创建 commit 的流程共享同一把锁和同一套 snapshot/stage/commit/rollback 语义。仅给现有函数
继续增加局部判断，无法解决跨流程竞态。

## 2. 审计方法与范围

- 建立 204 个文件的整库评审清单，覆盖 `bin/`、`lib/`、`tests/`、`viewer/`、模板和文档。
- 用 Python AST 提取 41 个顶层 Python 模块、49 条仓库内依赖边，并执行强连通分量检查。
- 核对 `ARCHITECTURE.md` 的分层、Source 子系统、公共 preflight、事务和验证矩阵。
- 检查 digest、Profile、provenance、postflight、INDEX、Wiki state 等写入路径。
- 检查 Profile/Bundle 的凭据拒绝、provider-neutral core 和兼容层边界。
- 统计 Skill 与 references 的读取路由，评估固定上下文和按场景上下文成本。
- 运行 Python 编译、shell 语法、架构契约、全量测试和 branch coverage。

本报告不读取、修改或复制独立知识库中的业务数据。

## 3. 现状架构评价

### 3.1 做得较好的部分

1. **事务边界显式。** `lib/digest_txn.py` 对目标文件快照、Git index 快照、原子替换、
   `git diff --check`、精确暂存路径和本地 commit 都有明确处理。
2. **provider-neutral core 基本成立。** AST 字符串检查和人工检查均未发现
   `digest_txn.py`、`kb_query.py` 新增 provider 名称分支。
3. **来源契约分层有效。** provider capture、adapter、Bundle model、transaction bridge、
   record projection 的职责能够解释当前新旧格式共存。
4. **查询有预算意识。** 默认只返回有限候选、有限一跳扩展，结构化记录优先读取派生 index，
   避免 Agent 直接加载完整 raw。
5. **测试覆盖面较广。** Source Bundle 一致性、Profile 严格校验、SnapshotStore、
   transaction rollback、provenance、viewer、CLI 真实进程形态均有测试。

### 3.2 主要结构压力

- `lib/source_capture.py` 2737 行、`lib/digest_txn.py` 2128 行、`lib/doctor.py` 1205 行，
  已成为修改热点。它们仍可维护，但新增 provider 或新事务类型会显著增加回归面。
- 多个模块复制 `_atomic_write`、Git 状态检查、index snapshot 和 rollback 逻辑，表面上各自
  原子，实际没有组成 KB 级原子系统。
- `tests/test_architecture_contract.py` 主要检查文档关键词、文件存在和 Mermaid 数量；
  `tests/test_source_architecture.py` 只检查 core 的 provider 字符串与 CLI 分发。它们无法验证
  文档声明的依赖方向、禁止环路和跨 writer 锁契约。

## 4. 核心缺陷

### 4.1 P1：KB 写入器没有共享仓库级互斥

证据：

- digest 使用 `.git/byteworker-digest.lock`：
  `lib/digest_txn.py:1768-1771`、`1925-1928`。
- Profile 使用 `.git/byteworker-source-profile.lock`：
  `lib/source_profiles.py:851-854`。
- provenance backfill 使用 `.git/byteworker-provenance.lock`：
  `lib/provenance_backfill.py:461-464`。
- update postflight 会修改节点、INDEX、journal 并 commit，但没有获取上述任何锁：
  `lib/update_postflight.py:219-274`。
- `bin/index.py` apply 会直接写 `INDEX.md`，也没有与事务 writer 协调。

这些流程都会操作同一个工作树或 Git index。两个流程可同时通过“暂存区为空”检查，然后分别
写 journal/INDEX、执行 `git add` 和 `git commit`。结果可能包括：

- 一个流程把另一个流程的文件带入自己的 commit；
- 后写入的 INDEX 或 journal 覆盖先写入结果；
- 一个流程失败恢复旧 Git index 时抹掉另一个流程的暂存结果；
- commit 成功但返回的 receipt 与实际 commit 内容不一致。

修复方向：

1. 新建基础设施模块，例如 `lib/kb_write_txn.py`。
2. 所有会修改 KB 工作树、Git index 或 commit 的入口先获取统一
   `.git/byteworker-write.lock`。
3. 统一执行 remote/staged/dirty 检查、目标快照、index 快照、精确 add、commit 和 rollback。
4. digest、Profile、backfill、postflight 只提供“计算 writes”和业务级写后校验。
5. 给不同 writer 的并发组合增加真实多进程测试，而不只测试同类 digest 并发。

### 4.2 P1：update postflight 会提交部分失败结果，且提交失败不回滚

`lib/update_postflight.py:241-243` 在任一 repair 返回 `ok=false` 时只追加 reason；随后
`261-272` 仍会在存在 allowed path 时写 journal 并 commit。也就是说，“自动修复执行失败”
和“提交已成功”可以同时发生。

此外，`_commit_repairs()` 在 `git add` 后若 `diff --check` 或 `git commit` 失败，
`run_postflight()` 只捕获 `RuntimeError` 并记录 reason，没有恢复文件或 Git index。后续所有
严格拒绝 staged 变更的事务会被阻断。与 digest/Profile/backfill 相比，这条写入路径缺少完整
事务保护。

修复方向：

- `failed` 非空时禁止进入 journal/commit。
- repair 前保存所有潜在目标和 Git index；任何 repair、复扫、journal、add、commit 失败都恢复。
- commit 前再次验证 staged path 精确等于允许集合。
- 将 postflight 接入统一 KB 写事务锁。
- 增加“一个 action 成功、一个 action 失败”和“git add 后 commit 失败”测试。

### 4.3 P1：Profile 与 Bundle 的 URL 凭据检测可绕过

`lib/source_profiles.py:141-149` 和 `lib/sources/models.py:158-163` 都只对 query key 做
`key.lower() in SENSITIVE_KEYS` 精确匹配。以下 URL 可通过当前 Profile 校验：

```text
https://user:password@example.com/docx/doc1?tenant_access_token=SECRET
```

问题包括：

- 未拒绝 `urlsplit(...).username/password`；
- `tenant_access_token`、`app_secret` 等常见派生名称不在精确集合中；
- Profile 和 Bundle 各维护一份相似但并不完全一致的敏感键集合。

Profile 和 raw/Bundle 会进入本地 Git。即使知识库禁止 remote，这仍违反“凭据不得持久化”
契约，并会扩大本机日志、备份和历史提交中的暴露面。

修复方向：

- 抽取共享的 URL 安全模块，Profile、Bundle、capture redaction 共用。
- 无条件拒绝 URL userinfo。
- 对规范化 key 使用明确的后缀/分词规则识别 `*_token`、`*_secret`、`*_password`、
  `authorization`、`signature`，同时维护 provider 允许的非凭据 token 白名单。
- 测试 userinfo、大小写、percent encoding、重复参数和常见 provider 凭据名。

### 4.4 P2：preflight 自更新后继续使用已加载旧代码

`lib/session_preflight.py:10-11` 在模块加载时导入 `report_automation` 和 `runtime_deps`；
`run_preflight()` 到 `162-183` 才执行可能 fast-forward 当前 skill 仓库的
`bin/update-check.sh`，之后又继续：

- 通过子进程运行更新后的 `bin/todo.py`；
- 调用进程内已加载的旧 `report_status`、`record_decision`；
- 使用更新前解析出的 runtime 数据。

`bin/update-check.sh:224` 对用户称“更新于下次使用生效”，但当前调用实际上已经混用了新脚本
和旧模块；`ARCHITECTURE.md:127-146` 的图则表现为 update/postflight 完成后继续所有检查。
若一次更新同时改变状态 schema、CLI 参数和库接口，本次 preflight 可能误报、写入旧 schema，
或执行不兼容组合。

修复方向：

- 最稳妥方案：update-check 返回 `updated=true` 后，当前 preflight 立即结束并请求 launcher
  `exec` 一个全新进程；同一调用只运行一个 commit 的代码。
- 次选方案：将 update-check 移到 Python 入口加载任何仓库模块之前，但仍需在更新后重新 exec。
- 用两个 fixture commit 构造 schema 不兼容更新，验证单次 preflight 不会混用版本。
- 统一文案和架构图，明确“本次重启生效”或“下次调用生效”。

### 4.5 P2：Profile provider 层形成反向依赖环

文档声明 `source_profiles.py -> source_profile_providers.py`，并要求 provider 规则不反向污染
生命周期模块。实际代码：

- `lib/source_profiles.py:513-518` 延迟导入 provider validators；
- `lib/source_profile_providers.py:13` 顶层反向导入 `SourceProfileError`。

AST 强连通分量检查确认二者组成真实环。当前依靠延迟导入避免初始化失败，但 validator 无法独立
导入和复用，错误契约也被生命周期模块拥有，新增 provider 时容易扩大耦合。

修复方向：

- 将 `SourceProfileError` 和公共校验 primitive 移入无 provider 依赖的
  `source_profile_contracts.py`。
- `source_profiles.py` 与 `source_profile_providers.py` 都只依赖 contracts。
- 增加 AST 架构测试：禁止 SCC、禁止 L3 provider validator 反向 import 生命周期模块。
- 架构测试应解析 import graph，而不是只验证文档包含模块名。

## 5. Token 与上下文成本

### 5.1 当前固定成本

`SKILL.md` 约 293 行、1349 个按空白统计的词。它承担入口协议、公共 preflight、命令路由、
digest 路由、报告、Todo、doctor 和治理规则，是每次技能加载的固定成本。

### 5.2 digest 路径成本

一次普通 digest 至少要求加载：

- `digest-core.md`：约 904 词；
- `digest-dependencies.md`：约 190 词；
- `digest-transaction.md`：约 583 词；
- `provenance.md`：约 273 词；
- 一个来源细则：约 160 至 611 词；
- 写入/失败规则在执行阶段还可能增加约 798 词。

加上 `SKILL.md`，常见 digest 的指令上下文约为 3.5k 至 4.7k 个空白词，中文实际 token 数通常
更高。大型输入、评论、白板或会议会继续叠加 references。当前路由避免了“一次加载全部
references”，方向正确，但公共 digest 规则仍有重复：

- 凭据、raw 不落 skill repo、失败不写入、原子事务等规则分散在 core、transaction、
  write-rules、error-handling 和 provider 文档。
- provider 文件重复描述 transport 错误与敏感 URL 规则。

### 5.3 降本建议

1. 保留 `SKILL.md` 的意图路由和不可违反规则，把命令示例、展示文案进一步移到 references。
2. 生成一个短小的 `digest-contract.md`，只包含所有来源共享且 Agent 必须遵守的输入/输出契约；
   transaction 的实现细节留给机器协议，不要求每次都进入模型。
3. provider reference 只描述差异，不重复公共安全与事务规则。
4. 为 references 建立“单一所有者”表和重复句检查，避免规则更新只改一处。
5. 机器可确定完成的校验继续下沉 CLI；Agent reference 只解释何时调用、如何处理有限回执。

目标可设为：普通单来源 digest 的额外 references 控制在当前成本的 60% 至 70%，同时不减少
安全约束。

## 6. 泛化与扩展性

### 6.1 新 provider

Bundle adapter registry 和 operation registry 已提供正确扩展点，但 Profile validator 仍是
`_validate_v2_profile()` 内静态导入三个函数并显式 dispatch。下一阶段可建立显式
`source_type -> validator` registry，注册对象同时声明：

- Profile selector/capture-policy validator；
- operation capabilities；
- Bundle adapter；
- request spec；
- legacy projection（仅兼容 provider 可选）。

registry 应是显式代码注册，不建议做目录反射式插件发现，以保持确定性和可审计性。

### 6.2 大文件拆分

建议按变化原因拆分，不按行数机械拆分：

- `source_capture.py`：将 Meego、Base、Aeolus transport/capture 移入 provider 模块，
  原文件保留兼容 facade。
- `digest_txn.py`：先抽出通用 KB write transaction，再分离 plan validation、
  provenance materialization 和 raw rendering。
- `doctor.py`：按 layout/node/link/report scanner 分模块，保留统一 report 聚合。
- `viewer/index.html`：将纯解析函数和 UI 状态拆成可直接 Node 测试的 JS 模块；构建仍可输出
  单文件 viewer。

## 7. 改进路线图

### 阶段 0：安全与原子性

1. 修复共享 URL 凭据判定，补安全测试。
2. 修复 postflight 的失败提交和 rollback。
3. 引入 KB 统一写锁，先接入 digest/Profile/backfill/postflight。

验收标准：跨 writer 多进程测试稳定通过；任何注入失败后 `git status --short` 与运行前一致；
凭据 URL 全部 fail closed。

### 阶段 1：自更新一致性与依赖治理

1. update 成功后重新 exec preflight。
2. 抽取 Profile contracts，消除 import cycle。
3. 新增 AST 依赖方向、SCC 和共享锁契约测试。
4. 同步 `ARCHITECTURE.md` 的 preflight 时序和写事务图。

验收标准：一个 preflight 进程只运行单一 commit；依赖图无环；架构测试能在人工注入反向 import
时失败。

### 阶段 2：复杂度与 Token 成本

1. 拆分 capture provider 模块和 transaction infrastructure。
2. 精简 digest references，建立公共规则单一所有者。
3. 对 Skill/reference 设内容预算和重复检查。

验收标准：普通 digest 指令加载成本下降 30% 左右；provider 新增不修改 transaction/query core；
兼容 facade 有明确移除条件。

## 8. 验证结果

| 检查 | 结果 |
|---|---|
| `python3 -m compileall -q bin lib tests` | 通过 |
| `bash -n`：`bin/*.sh` 与 `bin/byteworker`，共 9 个入口 | 通过 |
| 架构/Source/preflight/coverage 契约测试，15 项 | 通过 |
| 全量 unittest | 388 项通过 |
| branch coverage | 77.8%，通过 75% 门禁 |
| AST 模块依赖检查 | 发现 1 个真实跨模块环：`source_profiles` / `source_profile_providers` |
| provider-neutral core 字符串契约 | 通过 |

测试全绿不否定本报告问题：现有测试主要覆盖单流程成功/失败与同类事务，未覆盖跨 writer 并发、
postflight 部分失败、URL userinfo/派生 token key，以及真实跨版本自更新。

## 9. 文档与技术债务说明

本次只新增审计报告，不改变运行架构、schema、Skill 行为或兼容层，因此没有修改
`ARCHITECTURE.md`、`DESIGN.md`、`SKILL.md`。报告识别出的架构漂移包括：

- Profile validator 的真实反向依赖未在架构图体现；
- preflight 图未表达更新后进程内旧模块仍继续运行；
- 当前“并发”验证矩阵没有覆盖不同 KB writer 之间的互斥。

后续实现上述修复时，应在同一变更中同步架构图、失败边界和验证矩阵。

## 10. Agent 可执行性专项审计

### 10.1 总体判断

从 Agent 使用角度看，当前文档体系的优点是安全约束较强、主路径大多有明确入口，31 个
`references/*.md` 均可从 `SKILL.md` 或下游 reference 到达，没有孤儿文档或失效目标。标准
单来源 digest、飞书文档评论/白板、引用、Wiki 冷路径等路由已经写得比较明确。

但“文档可达”不等于“Agent 在正确时机会加载完整规则”。当前主要风险是：

1. 部分跨 session、子 Agent、无人值守流程只写“按普通流程”或“运行完整 routine digest”，
   没有显式列出标准流程的必读闭包。
2. 若宿主要求 reference 一旦选中就整文件读取，则 `commands.md` 的“按需读取对应小节”无法
   实现，`machine-protocol.md` 也会让一个简单查询加载所有工具的命令说明。
3. 标准 digest 之外的写入，大量依赖 Agent 手工完成 temp、INDEX、journal、精确 git add 和
   commit，稳定性取决于模型是否记住了分散规则。
4. 若干核心语义使用“重要”“长期”“明显”“稳定主题”等自然语言阈值，缺少统一判定表和
   机器可校验的中间结构。

因此当前状态应评价为：**普通交互式路径基本可用，但复杂、恢复、无人值守和跨模型执行的一致性
不足；上下文成本已经偏高。**

### 10.2 P1：委派、恢复和无人值守路径没有携带完整文档闭包

标准 digest 在 `SKILL.md:148-165` 明确要求读取：

- `digest-core.md`
- `digest-dependencies.md`
- `digest-transaction.md`
- `provenance.md`
- 对应来源细则

写入前还要读取 `write-rules.md`，失败时读取 `error-handling.md`，确定性 CLI 又要求
`machine-protocol.md`。

但以下二级入口没有完整传递这组要求：

- `references/digest-large.md:13` 声称子 Agent prompt “必须自足”，列出的文件却遗漏
  `provenance.md`、`write-rules.md`、`error-handling.md` 和机器协议。子 Agent 没有主对话记忆，
  这是实际漏读风险。
- `references/periodic-report.md:23-29` 只要求加载 `digest-routine.md`。
  `digest-routine.md` 会按来源指向 provider 细则，但没有明确要求重新加载标准 digest 的公共
  contract。自动任务模板同样只点名 report scheduling / periodic report。
- `references/wiki-digest-jobs.md:31` 只说每页“按普通 `feishu_doc` 流程处理”。跨 session
  恢复任务时，Agent 可能只加载 job 文档，而不会自动回到 `SKILL.md` 重建完整 digest 闭包。

这些路径最容易出现“规则确实存在，但执行 Agent 没读到”的问题。

改进建议：

1. 不再用“普通流程”“标准流程”作为唯一跳转描述。
2. 给每个可独立启动的入口声明完整 `requires`，例如：

   ```yaml
   workflow: wiki_digest_page
   requires:
     - machine-protocol-core
     - digest-contract
     - digest-feishu-doc
     - write-contract
   ```

3. 子 Agent prompt、自动任务模板和恢复任务只引用一个可独立执行的 workflow contract，不手工
   复制一组容易漂移的文件名。
4. 增加静态测试，验证每个 entrypoint 的 transitive closure 包含安全、provenance、事务和失败
   contract。

### 10.3 P1：reference 粒度与宿主的整文件加载规则不匹配

`SKILL.md:191-207` 把 search、update、brief、dashboard、context 全部路由到
`references/commands.md`，并写“按需读取对应小节”。如果宿主要求选中的 reference 必须读完，
执行一次 search 实际会同时加载 update、dashboard 和 context 的全部说明。

同样，`references/machine-protocol.md` 约 10k 字符，包含 digest、query、doctor、Todo、
report、source、Wiki、digest-job 的所有命令示例。任何一次确定性 CLI 调用都被要求加载该文件，
即使实际只需要理解四字段 envelope 和一个命令。

按当前明确路由估算，不含业务原文、节点、`context.md` 和模板：

| 场景 | 文件数 | 字符数 | 空白分词数 |
|---|---:|---:|---:|
| 只加载 `SKILL.md` | 1 | 14,027 | 1,279 |
| search | 4 | 33,389 | 3,210 |
| Todo 写入 | 4 | 30,001 | 2,901 |
| 飞书文档 digest 最小闭包 | 10 | 57,799 | 5,529 |
| 带评论/白板/立场/Todo 的文档 digest | 13 | 63,998 | 6,171 |
| IM Inbox | 6 | 42,374 | 4,213 |
| 自动报告基础闭包 | 8 | 47,293 | 4,618 |

中文 token 数不能由空白词数直接换算，但这些字符规模已经足以说明固定指令会显著挤压业务正文、
历史节点和候选 plan 的上下文预算。

改进建议：

- 将 `commands.md` 拆为 `command-search.md`、`command-update.md`、`command-brief.md`、
  `command-dashboard.md`、`command-context.md`。
- 将 `machine-protocol.md` 缩成约 2k 字符的 envelope / exit-code 公共 contract；具体参数通过
  `bin/byteworker <tool> --help`、`source capabilities`、`bundle-spec` 等机器接口发现。
- 将 provider 错误放回对应 provider reference；`error-handling.md` 只保留跨来源公共失败策略。
- 将 `SKILL.md` 收缩为“触发路由 + 不可违反的不变量 + workflow 入口”，目标先降到当前字符数
  的 50%-60%。

### 10.4 P1：非 digest 写入仍把事务细节交给 Agent

标准 digest 已把 hash、校验、原子写入、INDEX、journal、精确暂存和 commit 下沉到
`digest-txn.py`。但以下路径仍要求 Agent 按 prose 自行操作：

- update：合并节点、更新 INDEX、追加 journal、commit；
- context：识别章节后手工 temp-then-move、journal、精确 git commit；
- dashboard：保留手动段、重算派生段、写 journal、commit；
- 日报/周报/IM 报告：保留“手动补充”章节、覆盖文件、写 journal、精确暂存、commit；
- index/links maintenance：脚本只改文件，Agent 再完成 journal 和 Git 收尾；
- routine 无增量：Agent 仍要维护 journal、`.last-routine-digest` 和 INDEX 状态。

这些步骤具有确定输入和副作用，不需要 LLM 推理。让 Agent 自行拼 shell / 文件编辑会造成：

- 忘记保留手动区块；
- journal 已写但报告失败，或报告已写但 commit 失败；
- `git add` 路径不精确；
- 不同模型对 INDEX 增量更新方式不一致；
- 错误时无法可靠恢复。

改进建议：

1. 建立统一 `kb-write` / `kb-mutate` 事务 API，接收明确 writes、expected hashes、journal event
   和 commit message。
2. 报告使用“Agent 生成结构化 report draft，Python renderer 保留手动章节并提交”的模式。
3. context 使用 section-aware patch 命令；dashboard 使用确定性 renderer。
4. update 复用版本化 mutation plan 和候选校验，不允许 Agent 直接修改节点文件。
5. Agent 只负责语义内容和用户裁决，文件生命周期全部由 Python 负责。

这项改进同时解决第 4.1 节的统一写锁问题，是最高收益的架构收敛点。

### 10.5 P1：冲突策略存在互相矛盾的说明

- `references/digest-core.md:78-80`：发现冲突后高亮并等待用户裁决，不静默覆盖。
- `references/citations.md:128-130`：新旧来源冲突时并列引用，不得只引用较新来源。
- `references/commands.md:55-56`：update 先做同一冲突检测，下一步却规定“以更晚的来源为准”。

Agent 可以合理地走出三种不同结果：暂停询问、自动采用新值、并列保留不更新。该差异会直接改变
知识库内容，不是文案问题。

改进建议：

- 建立唯一冲突决策表，至少区分：
  - 同一事实的新版本修订；
  - 两个独立来源的事实冲突；
  - 生效 decision 的明确 supersede；
  - 用户直接输入对既有内容的纠正。
- 只有带明确 revision / supersede 关系时才自动推进；独立来源冲突默认并列并询问。
- 其它文档只引用 policy ID，不再各写一套自然语言版本。
- mutation plan 必须带 `conflict_disposition` 和证据，Python validator 拒绝未声明的覆盖。

### 10.6 P2：关键语义阈值过于开放

当前仍由 Agent 自由判断的高影响概念包括：

- “长期项目”“稳定跨记录主题”“明确决策”“重大变化”；
- 哪些人属于“关键参与方”，是否推断其利益/动机；
- IM thread 的 `importance`、`relevance_to_user` 以及两个布尔晋升结果；
- 输入类型无法判定时是 `area` 还是 `event`；
- 搜索何时属于候选不足、该换哪些近义词、journal 扫描范围多大。

尤其 `references/im-inbox-summary.md:159-191` 给出了 JSON 形状，却没有规定分数范围、阈值和
reason code；`references/error-handling.md:25` 在无法分类时直接采用类型倾向，也没有要求用户
确认。这些路径在不同模型、不同上下文长度下容易产生明显差异。

改进建议：

1. 为知识晋升定义 reason code 和最小证据：
   `explicit_decision`、`dated_status_change`、`cross_record_theme`、`user_owned_action` 等。
2. IM 明确 `importance/relevance` 的范围、评分锚点和布尔阈值；Python 校验 schema 和阈值。
3. 推断“利益/动机”默认不持久化，除非存在直接陈述或多条可定位证据；否则只保留观察。
4. 无法分类时返回有限候选让用户选，不自动落到 `area/event`。
5. Agent 输出 `semantic-plan/v1`：每个节点/事实带 type、reason、evidence、confidence、
   conflict disposition；validator 先检查再允许生成候选 Markdown。

### 10.7 P2：`context.md` 没有机器执行的大小和按意图裁剪

模板只写“保持简短”，没有字符、条目、过期或 section 预算。`SKILL.md` 要求 digest、search、
update、brief、dashboard、todo 和报告读取整个文件，但这些意图需要的信息不同：

- Todo 主要需要身份、时区和提醒偏好；
- search 主要需要当前重点和职责；
- person 消解需要身份；
- 报告需要职责、重点和主管方向。

随着用户持续维护，完整加载会增加固定上下文并把旧重点继续带入语义判断。

改进建议：

- 增加 `context view --intent <todo|search|digest|report>`，由 Python 解析固定七章节并返回有限 JSON。
- 对每节设软上限和 stale 提示；超过上限要求用户归档，而不是静默截断。
- 保留原始 `context.md` 作为真相源，Agent 日常只消费按意图投影。

### 10.8 测试缺口

现有静态测试主要验证：

- Markdown 链接目标存在；
- `SKILL.md` 含某个 reference 名称；
- 文档含关键短语；
- Wiki 等冷路径没有被直接塞回核心模块。

这些测试无法发现：

- entrypoint 的传递闭包遗漏；
- 一个场景实际需要加载多少文本；
- 两份 policy 对同一问题给出相反动作；
- 子 Agent / 自动任务 prompt 是否自足；
- 语义输出在相同 fixture 上是否稳定。

建议新增：

1. `tests/test_agent_route_contract.py`：读取 route manifest，验证每个 intent/entrypoint 的必需闭包、
   条件路由、无环和文件存在。
2. 场景上下文预算测试：对 search、todo、各 provider digest、inbox、auto report 设置字符上限。
3. 自动任务与子 Agent prompt 完整性测试：必须引用单一 workflow contract。
4. policy 单一所有者测试：冲突、写事务、引用、权限等核心 policy 只有一个 authoritative owner。
5. 固定小型 fixture 的 semantic-plan golden tests；CI 不要求文案逐字一致，但要求节点类型、
   reason code、证据覆盖和冲突动作一致。

## 11. Agent 侧改造优先级

### A. 先修稳定性

1. 统一 KB mutation API，把非 digest 文件/Git 操作下沉 Python。
2. 统一冲突策略。
3. 修复 large digest、auto report、Wiki resume 的必读闭包。

### B. 再降上下文

1. 拆分 `commands.md`。
2. 精简并拆分 `machine-protocol.md`。
3. 压缩 `SKILL.md`，增加 route manifest 和预算测试。
4. 按 intent 投影 `context.md`。

### C. 最后收敛语义自由度

1. 引入 `semantic-plan/v1`。
2. 为晋升、IM 精判、冲突和推断增加 reason code / evidence / threshold。
3. 用 golden fixture 验证跨模型或多次运行的一致性。

## 12. Remediation 状态

本节记录审计后的实现状态；上文行号和行为描述保留为审计基线，不代表当前代码。

| 审计项 | 状态 | 当前实现与验证 |
|---|---|---|
| KB writers 使用不同锁 | 已修复 | `lib/kb_write_txn.py` 提供 `.git/byteworker-write.lock`；digest、Profile、backfill、postflight、mutation、Todo 与维护入口共用 |
| postflight 部分失败/提交失败不回滚 | 已修复 | repair 失败停止提交；文件、Git index、HEAD 均有 rollback 测试 |
| URL 凭据检测可绕过 | 已修复 | `lib/credential_safety.py` 统一检查 userinfo/query/fragment/编码键，并保留资源 token 白名单 |
| preflight 混用新旧版本 | 已修复 | stable shell 在加载任何 Byteworker Python 模块前完成 update-check，再 exec 当前 launcher |
| Profile validator import cycle | 已修复 | 公共异常移入 `source_profile_contract.py`，静态 import 契约禁止反向依赖 |
| 委派/恢复/无人值守文档闭包缺失 | 已修复 | `workflow-routes.json` 可递归展开；large worker、Wiki resume、自动报告显式解析 route |
| Skill/commands/machine protocol 过大 | 已修复 | router/protocol 精简，commands 拆为独立 reference，并设置字符预算测试 |
| 非 digest 写入依赖 Agent 手工副作用 | 已修复 | `byteworker-kb-mutation/v1` 负责 update/context/dashboard/report；doctor/index 维护也统一事务收尾 |
| 冲突策略矛盾 | 已修复 | `references/conflict-policy.md` 为唯一决策表；较新来源本身不构成 revision |
| IM/晋升/参与方推断阈值不稳定 | 已修复 | `semantic-policy.md`、`byteworker-im-semantic/v1`、validator 和禁止兜底契约测试 |
| context 无裁剪与预算 | 已修复 | `byteworker-context-view/v1` 按 intent 投影，12k warning、24k fail closed |
| route/context/并发/rollback 测试缺口 | 已修复 | 新增 route、context、semantic、mutation、共享锁和 postflight rollback 测试 |

架构变化已同步到 `ARCHITECTURE.md`，瞬时 mutation/context/semantic schema 与锁定决策已同步到
`DESIGN.md`；Agent 行为同步到 `SKILL.md`、对应 references 和自动报告模板。
