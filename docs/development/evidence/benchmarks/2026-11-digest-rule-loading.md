# 2026-11-03 普通单文档 digest 规则加载对比

本记录是静态规则测量，不是模型实际 usage、费用或墙钟耗时基准。
基线为 `15471df430f3d94859956bc82636eaf9f1a76e03`，改后为本次工作区实现。
完整路径、逐文件 SHA-256 和计数见 [JSON 回执](2026-11-digest-rule-loading.json)。

## 同一场景与统计口径

新上下文首次执行普通 feishu_doc digest，包含全部评论；无白板、routine、并发 worker、恢复、
批量提交或内部资料 feature。规则包括 SKILL 和闭包内全部文件，各计一次；文件间以两个换行连接。
正文、评论原文、context 投影、节点模板、外部 lark skill、历史对话、tool envelope 元数据与缓存均
不计入两边。健康 preflight 不需 session-preflight，因此基线也未将截图中的该文件算入。

使用同一 `tiktoken:o200k_base` 精确分词；这是该分词器下的文件 token 数，不是宿主实际计费值。
路由发现基线是整份 workflow-routes.json 原文，改后是 resolve 的紧凑 data JSON（不计 envelope）。

| 指标 | 改前 | 改后 | 降幅 |
|---|---:|---:|---:|
| 必读规则文件（含 SKILL） | 16 | 13 | 18.8% |
| 必读规则字符 | 67,719 | 44,053 | 34.9% |
| 必读规则 tokens | 30,903 | 20,451 | 33.8% |
| 路由发现 tokens | 3,203 | 121 | 96.2% |
| 规则与路由发现总 tokens | 34,106 | 20,572 | 39.7% |

## 行为变化与保留边界

- resolve 复用 inspect 的 resolver，只返回所选路径，Agent 不整读所有功能路由和预算。
- core 删去来源分支和重复长篇细则，普通执行交接放 digest-inline；flow prepare 的 packet 和计划
  直接复用，不重复运行底层预处理与 dependency/semantic planner。
- 并发/分析、日志恢复/usage、批量事务手册分别按 parallel_execution、recovery、batch 加载。
- 依赖扩展确认、唯一冲突策略、语义晋升/推断限制、组织范围/人员身份、全部评论、出处与候选
  evidence、双向 links、baseline、原子事务和 committed receipt 仍保留。完整错误闭包不截断。
- 大型 worker、Wiki resume 和 Dreaming routine 保持独立自足。已有 v1 兼容入口保留；无新 provider
  分支、KB 目录或持久化 schema。架构同步见 ARCHITECTURE §2.6，临时回执记入 DESIGN §29。
- 对照最新 master 复核后补回 resume_failed 的历史失败来源人工恢复闸门（recovery 同时加载事务
  和日志细则），并明确 department_path 只连接命中已有 org；均有回归契约测试。
- 最新 master 的 discovery-completion 公共收尾保持必读，基线与改后均计入，所以文件数为 16 → 13。

## 复核方法与回归门禁

改后用 `workflow-budget inspect --workflow digest --source-type feishu_doc` 复核 static_rules_tokens；
无 tiktoken 时工具会明确使用保守估算，不能与上表混比。基线文件通过 JSON 的 baseline_commit 和
paths 逐一 `git show <commit>:<path>` 读取，再以相同分词器、连接方式计数。路由发现用 manifest
原文或 `json.dumps(resolve_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`。

`test_workflow_budget` 限制普通闭包最多 13 文件、46,000 字符和 48,000 保守估算 tokens，验证
resolve/inspect 闭包一致、resolve 不读规则正文/计 token、条件增量和非法 route/source/feature 拒绝。
路由和执行契约测试验证完整安全 policy、条件手册与 worker/routine 入口，原有事务/证据测试继续运行。

依赖并发测试原先用重复 x 作为长上下文，在精确分词器下未达到墙钟收益阈值；改用确定性、变化的
标识串，使有/无可选分词器均覆盖原有并发分支。阈值和产品算法没有修改。
当前基线已包含跨平台 help 和路径比较测试修复，本变更沿用；未改变运行时的路径或环境发现逻辑。

本测量不证明实际飞书调用、语义产出质量或总耗时已按同样比例改善；未对用户 KB 执行 digest。

## 最终验证

- 最新 master 基线 `15471df430f3d94859956bc82636eaf9f1a76e03`，保留其交互收尾和已有跨平台修复。
- 架构、路由、CLI、身份/范围、出处、flow/事务与并发专项：128 项通过。
- 无 tiktoken 的 workflow 预算检查：11 项通过，预算门禁与最新 master 一致。
- 全量 `coverage run -m unittest discover -s tests -p 'test_*.py'`：711 项通过，66.320 秒。
- `.coveragerc` 的 bin/lib line/branch + subprocess 覆盖率：78.1%，通过 75% 门禁。
- Python compileall、bin/byteworker 与全部 bin/*.sh 的 bash -n、git diff --check：通过。
- resolve/inspect 使用同一 resolver，普通闭包为 13 文件；所有 source type、feature 与错误闭包
  的预算场景均通过测试。规则文件 SHA-256 与 JSON 回执一致。

最终复核逐项对照如下；这里验证规则与执行契约覆盖，不代替真实来源的模型语义质量评估。

| 约束 | 必需位置 / 条件 |
|---|---|
| 外部依赖正文须授权、仅一跳、不自动扩展 | digest-dependencies 始终必读，core 保留用户闸门 |
| 完整正文、全部评论/回复与 coverage；内嵌白板只取结构 JSON | feishu_doc 始终含 doc/comments；白板触发 feature |
| 主张/意图/推断边界、晋升最低证据与独立冲突裁决 | semantic-policy、conflict-policy 始终必读且内容未改 |
| 人员唯一身份、组织范围与负责人证据、禁止由协作推导归属 | write-rules 始终必读，core 保留人员消解及已有 org 连边限制 |
| 关键事实 evidence、primary_source、locator、弱证据降级 | provenance 始终必读且内容未改 |
| 历史失败来源人工恢复，不自动覆盖/提交 | core/inline 在 preflight_state=resume_failed 时停止；recovery 含事务与日志细则 |
| raw 不覆盖、完整候选、baseline、双向 links、锁内复验与原子回滚 | core/inline + 既有事务代码；事务/共享锁实现未改 |
| fresh worker、完整分片覆盖、单一 reducer、无主/子重复语义分析 | SKILL 全局规则和 parallel_execution / 独立 worker 闭包 |

测试和可选 tokenizer 使用仓库外临时 venv；未增加产品依赖、兼容分支或持久化 schema。
现有 v1 兼容入口仍保留。测试使用 TMPDIR=/private/tmp，不访问用户知识库或真实飞书来源。
