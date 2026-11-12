# byteworker · 单来源执行契约

普通 digest 的工具交接使用本文件；安全、语义和来源细则仍按路由完整加载。
工具不做语义裁决，hash、幂等、schema、锁、INDEX、journal、commit 和 rollback 由工具负责。

## 抓取与一次准备

先按 `digest-flow.md` start，保存唯一 run_id/work_dir。飞书正文/评论在同一个
`byteworker-digest-capture-plan/v1` 的 jobs 中抓取：

```json
{"schema_version":"byteworker-digest-capture-plan/v1","max_workers":3,"jobs":[
  {"id":"body","runner":"lark","args":["docs","+fetch","--doc","<URL>","--detail","with-ids","--format","json"],"output":"<WORK_DIR>/body.json","max_attempts":2},
  {"id":"comments","runner":"comments","args":["--url","<URL>","--jobs","3"],"output":"<WORK_DIR>/comments.json","max_attempts":2}
]}
```

runner=lark 的真实参数先按来源 skill 或 --help 确认，不能照抄过时示例。正文发现白板后再执行
第二波 job；顺序分页仍串行。capture 并发最多 3，限流降到 1，每 job 最多尝试 2 次。
component 失败必须停止，不能用部分结果冒充完整。artifact/request 权限 0600，目录 0700，
只能在私有 work_dir 或系统临时目录，不进入 skill 仓库。

`source bundle-spec --source-type <TYPE>` 按需返回 request 字段；request 只引用 artifact 绝对路径。
正文用 verbatim，评论/白板用 canonical-json；抓取 wrapper 可用 json_pointer 选择正文字符串。
identity、coverage、anchors、provider_metadata 由来源 adapter 交给 Bundle，不能伪造空评论。
然后只执行一次 `digest-flow prepare`，不重复调用底层 bundle/preflight/analysis prepare/planner。

先检查 preflight_state：noop 结束；resume_failed 加 recovery 核查历史 pending/failed raw，人工恢复前
不生成候选或提交，不得覆盖旧 raw。仅 new_source/new_version 读取 analysis packet 和计划继续：

- dependency_candidates 只用于依赖判断，不授权读取依赖正文。
- sections[].text_items 是去除样式/坐标后的正文、评论、回复、白板文本，保留 component/path；
  participant_ids 一次批量解析，anchors 按 id 定点读取。不要打印整包或探测 schema 循环。
- plan 选 inline 时只处理唯一 shard；选 parallel 时，执行前通过 resolve 加 parallel_execution，
  读取 `digest-concurrency.md` / `digest-analysis-pipeline.md` 和角色闭包。最多 4 worker、完整
  coverage、单一 reducer、用户闸门和一次事务规则保持。不得改阈值、跳过 planner 或截断证据。
  预算核算使用模型实际读取的 shard，勿把整个 analysis packet 再带入。超限时按
  `workflow-budgets.md` 复核投影与分片消耗，确需放宽才交用户确认。

## 冲突交接

语义分析完成才写短请求，不复制原文：

```json
{"schema_version":"byteworker-conflict-query/v1","source_uid":"<UID>","queries":[{"id":"fact-1","query":"<短查询>"}]}
```

最多 32 条，每条最多 240 字符。只运行一次召回，再规划 conflict：

```bash
bin/byteworker kb-query conflict-search --kb "<KB>" --request "<WORK_DIR>/conflict-query.json" --limit-per-query 3 --max-nodes 20
bin/byteworker digest-parallel plan --stage conflict --input "<CONFLICT_CANDIDATES>" --out-dir "<WORK_DIR>/conflict"
```

召回只扫描 KB 节点一次，返回同源节点和有限 snippet；工具不判冲突。按 planner 的 inline/parallel
结果和 `conflict-policy.md` 裁决，不能让 semantic worker 各扫一遍 KB。

## 候选与提交

`digest-plan/v2` 使用 `templates/digest-plan-v2.json`：source_bundle 指向 Bundle；raw 声明唯一
raw_id/path、excluded_dependencies；顶层 provenance 只填 enrichment，不复制 anchors/source。
nodes 每项声明 op/path/candidate 和 evidence，新节点至少一条；主记录指定 primary_source。
更新项附读取时的 base_sha256 和 conflict_disposition，覆盖须附 conflict_evidence；保留既有
正文、sources、evidence 和双向 links，确需删除须显式 removal 与理由。完整候选不使用文本替换 DSL。
事务自动拼 raw、digest_targets、primary_source_url 和证据表，Agent 不重复手写。
无业务增量按 `write-rules.md` 保留正文及 evidence，不为新 raw 编造 E 条目。
字段拿不准时按 `digest-txn --help` 或错误诊断加载完整 `digest-transaction.md`，不猜字段。

只执行 `digest-flow commit`；它在锁内复验，原子更新与失败回滚。禁止手工 git add/commit、写
INDEX/journal、改旧 raw 或绕过错误。只认 committed + commit hash；noop 不重复写。

## 日常阶段日志

flow 自动记录 classify/capture/bundle/preflight/analysis_prepare/transaction/finalize。
Agent 只为 dependency_review、semantic_analysis、conflict_review、candidate_generation 成对记录：

```bash
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" --stage semantic_analysis --status started
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" --stage semantic_analysis --status completed
```

失败记录 failed，并用 `digest-run complete --status failed --error-code <CODE>` 关闭 run。
等待用户先关闭当前 stage，再 `digest-run wait --reason-code DEPENDENCY_APPROVAL_REQUIRED`（依赖）
或 CONFLICT_DECISION_REQUIRED（冲突）；答复后 `digest-run resume`，取消用 complete --status cancelled。
命令均带同一 kb/run-id。日志只含固定阶段、detail code 与非负计数，禁止业务正文、标题、人员、
URL、凭据、argv 或自由文本。宿主有模型 usage 时加 recovery 读完整日志协议，区分 measured 与
estimated；不得伪造实测值。恢复、stale 或日志诊断同样加 recovery；健康 preflight 不读
`session-preflight.md`。
