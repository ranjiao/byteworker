# byteworker · digest 全流程耗时日志

每个用户输入对应一个稳定 `run_id`。日志覆盖 Agent 语义层和确定性事务层，用来回答“时间花在
抓取、依赖判断、语义分析、候选生成还是事务写入”，不是知识证据，也不保存业务正文。

## 1. 输入到达后立即开始

在读取正文、调用外部来源或分类前运行：

```bash
bin/byteworker digest-run start --kb "<KB>" --source-ref "<稳定非敏感输入标识>"
```

保存返回的 `data.run_id`，本次 input 的所有后续阶段和 `digest-txn` 都复用它。`source-ref` 可省略；
若提供只持久化 SHA-256，不保存原值。不得把带凭据或敏感 query 的 URL 放进命令参数。

分类本身也必须成对打点；完成时补充实际来源类型：

```bash
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage classify --status started
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage classify --status completed --source-type feishu_doc
```

## 2. 阶段必须成对记录

对实际发生的阶段在动作前记 `started`，完成后记 `completed`；失败记 `failed`。固定阶段为：

| stage | 记录范围 |
|---|---|
| `classify` | 输入类型、workflow 与 feature 路由 |
| `capture` | inspect/auth 后的完整正文、评论、白板、分页或快照抓取 |
| `bundle` | SourceBundle、coverage、components 与 anchors 构造 |
| `preflight` | payload hash、幂等和同源状态；由 `digest-txn --run-id` 自动记录 |
| `analysis_prepare` | 一次性 analysis packet 构造；由 `digest-analysis --run-id` 自动记录 |
| `dependency_review` | 只消费 packet 的依赖候选，完成重要性判断和范围闸门 |
| `semantic_analysis` | 只消费 packet，形成事实、实体、决策、立场、evidence 和冲突 query |
| `conflict_review` | 单次 KB 扫描、同源定位、有限候选读取和冲突分类 |
| `candidate_generation` | 候选节点、provenance mapping 和 plan 生成 |
| `transaction_validate` | 独立排障 validate；由 `digest-txn --run-id` 自动记录 |
| `transaction` | execute 的校验、写入、INDEX、journal 和 commit；自动记录 |
| `finalize` | receipt 核验与紧凑结果；由 `complete` 记录 |

标准顺序是 `preflight → analysis_prepare → dependency_review → semantic_analysis →
conflict_review → candidate_generation`。不要在 `dependency_review` 中记录 worker 启动/规则加载，
也不要把评论解析、白板遍历、人员消解、事实抽取或模板加载记入 `conflict_review`。预处理与单扫描
召回命令见 `references/digest-analysis-pipeline.md`。

示例：

```bash
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage capture --status started --detail-code FETCH_SOURCE_COMPONENTS
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage capture --status completed --detail-code SOURCE_COMPONENTS_READY \
  --component-count 3 --input-bytes 1048576 --page-count 12 --retry-count 1
```

`detail-code` 只能是大写稳定机器码。可选 metrics 只允许非负整数：`item_count`、
`component_count`、`input_bytes`、`output_count`、`warning_count`、`retry_count`、`page_count`、
`node_count`、`evidence_count`。禁止写标题、人员/群名、正文摘要、URL、token、完整
argv、stdout/stderr 或自由文本错误。阶段超过 60 秒仍按既有用户 heartbeat 规则回显；不要为了日志
主动轮询，阶段完成后一次性记录真实耗时即可。

## 3. 事务自动打点与结束状态

所有 transaction 命令都传同一个 `--run-id`：

```bash
bin/byteworker digest-txn preflight --kb "<KB>" \
  --manifest "<BUNDLE_OR_PLAN>" --run-id "<RUN_ID>"
bin/byteworker digest-txn execute --kb "<KB>" \
  --manifest "<PLAN>" --run-id "<RUN_ID>"
```

不要再手工记录 `analysis_prepare` / `preflight` / `transaction_validate` / `transaction`，否则会形成重复开放阶段。
最终 receipt 确认后必须结束 run：

```bash
bin/byteworker digest-run complete --kb "<KB>" --run-id "<RUN_ID>" \
  --status committed --node-count 3 --evidence-count 12 --warning-count 0
```

幂等命中用 `--status noop`。任一阶段失败时，先把当前阶段记为 `failed`，再用稳定 error code 结束：

```bash
bin/byteworker digest-run complete --kb "<KB>" --run-id "<RUN_ID>" \
  --status failed --error-code SOURCE_CAPTURE_FAILED
```

用户暂不继续或拒绝扩展时用 `cancelled` 和稳定原因码。不得留下明知已终止的 `running` run。

## 4. 排查

```bash
bin/byteworker digest-run list --kb "<KB>" --limit 20
bin/byteworker digest-run show --kb "<KB>" --run-id "<RUN_ID>"
```

`list` 直接返回总耗时和最慢阶段；`show` 返回完整阶段时间线。日志位于
`state/digest/run-logs/<UTC-date>[-NNNN].jsonl`，目录/文件权限为 `0700/0600`，单文件 5 MiB
轮转，默认保留 30 天，并通过本地 `.git/info/exclude` 的 `/state/` 排除在 KB Git 之外。
