# byteworker · digest 全流程耗时日志

每个用户输入对应一个稳定 `run_id`。日志覆盖 Agent 语义层和确定性事务层，用来回答“时间花在
抓取、依赖判断、语义分析、候选生成还是事务写入”，不是知识证据，也不保存业务正文。

## 1. 标准路径自动开始

标准 digest 在读取正文、调用外部来源或分类前运行：

```bash
bin/byteworker digest-flow start --kb "<KB>" \
  --source-type "<SOURCE_TYPE>" --source-ref "<稳定非敏感输入标识>"
```

保存返回的 `data.run_id`，本次 input 的所有后续阶段和事务都复用它。`source-ref` 可省略；
若提供只持久化 SHA-256，不保存原值。不得把带凭据或敏感 query 的 URL 放进命令参数。
flow 自动记录 classify，标准路径不得再为确定性阶段手工成对打点。

只有迁移旧 run 或诊断底层日志时，才直接使用以下命令；此时调用方负责成对状态：

```bash
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage classify --status started
bin/byteworker digest-run stage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage classify --status completed --source-type feishu_doc
```

## 2. Agent 阶段成对记录

`digest-flow` 自动负责 classify/capture/bundle/preflight/analysis_prepare/transaction/finalize。Agent
只对实际发生的 dependency_review/semantic_analysis/conflict_review/candidate_generation 在动作前记
`started`，完成后记 `completed`，失败记 `failed`。固定阶段为：

| stage | 记录范围 |
|---|---|
| `classify` | 输入类型、workflow 与 feature 路由 |
| `capture` | inspect/auth 后的完整正文、评论、白板、分页或快照抓取；可含有界并发 |
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

`detail-code` 只能是大写稳定机器码。阶段 metrics 只允许非负整数：`item_count`、
`component_count`、`input_bytes`、`output_count`、`warning_count`、`retry_count`、`page_count`、
`node_count`、`evidence_count`、`worker_count`、`shard_count`。并发阶段只由 coordinator 在外层
成对记录一次，worker 不打点，duration 是真实墙钟时间。禁止写标题、人员/群名、正文摘要、URL、
凭据/token 值、完整 argv、stdout/stderr 或自由文本错误。阶段超过 60 秒仍按既有用户 heartbeat
规则回显；确定性长阶段确实仍在推进时可同时运行一次
`digest-run heartbeat --run-id <RUN_ID> --stage <STAGE>`，但不要为刷新日志主动轮询。

## 3. 模型 usage 回执

宿主每完成一次模型调用，在获得 usage 后上报一次；宿主不提供实测值时可以上报 `estimated`，但
不得把估算伪装成 `measured`：

```bash
bin/byteworker digest-run usage --kb "<KB>" --run-id "<RUN_ID>" \
  --stage semantic_analysis --worker-role semantic_worker \
  --usage-source measured --call-id "<STABLE_OPAQUE_CALL_ID>" \
  --input-tokens 1200 --cached-input-tokens 900 \
  --output-tokens 150 --reasoning-tokens 40
```

`call-id` 只接受稳定 opaque id，日志仅保存 SHA-256；同一 run 重报同一 id 时幂等，不重复计数。
worker role 固定为 `coordinator/dependency_worker/semantic_worker/conflict_worker/final_reducer`。
usage 事件可在 run 终态后补报，以覆盖最终响应；它不改变 committed/noop/failed/cancelled 状态或
duration。`reasoning_tokens` 作为 `output_tokens` 的细分显示，`total_tokens` 不重复相加。

usage 只记录固定枚举和非负整数。禁止记录模型名、prompt、正文、标题、URL、原始 call id、凭据、
完整 argv 或自由文本。`list/show` 聚合总 usage 和逐 stage usage，并分别报告 measured/estimated
调用次数；没有宿主回执时保持 0，不猜真实计费。

## 4. 事务自动打点与结束状态

标准路径把 plan 交给 flow；它自动记录 transaction、核验 receipt 并结束 run：

```bash
bin/byteworker digest-flow commit --kb "<KB>" --run-id "<RUN_ID>" \
  --plan "<WORK_DIR>/digest-plan.json"
```

不要再手工记录 `analysis_prepare` / `preflight` / `transaction_validate` / `transaction`，否则会形成重复开放阶段。
以下底层完成命令只用于迁移、恢复或诊断，不是标准路径：

```bash
bin/byteworker digest-run complete --kb "<KB>" --run-id "<RUN_ID>" \
  --status committed --node-count 3 --evidence-count 12 --warning-count 0
```

幂等命中用 `--status noop`。任一阶段失败时，先把当前阶段记为 `failed`，再用稳定 error code 结束：

```bash
bin/byteworker digest-run complete --kb "<KB>" --run-id "<RUN_ID>" \
  --status failed --error-code SOURCE_CAPTURE_FAILED
```

需要用户授权、范围或冲突裁决时，先关闭当前 stage，再显式暂停：

```bash
bin/byteworker digest-run wait --kb "<KB>" --run-id "<RUN_ID>" \
  --reason-code DEPENDENCY_APPROVAL_REQUIRED
bin/byteworker digest-run resume --kb "<KB>" --run-id "<RUN_ID>"
```

`wait` 只接受固定 reason code，等待区间不计入 active duration；收到用户答复后必须 resume，成功终态
不能从 waiting_user 直接跳过闸门。用户明确不继续时用 `cancelled` 和稳定原因码。

## 5. 排查

```bash
bin/byteworker digest-run list --kb "<KB>" --limit 20
bin/byteworker digest-run show --kb "<KB>" --run-id "<RUN_ID>"
```

`list` 直接返回 active duration 和最慢阶段；`show` 返回完整阶段时间线。非终态 run 超过固定 6 小时
没有 lifecycle heartbeat 时显示 `stale`，duration 截止最后 lifecycle event，不再无限增长；恢复工作先
运行 `digest-run resume`。`usage` 补报不属于 heartbeat，不能把 stale run 伪装为 active。日志位于
`state/digest/run-logs/<UTC-date>[-NNNN].jsonl`，目录/文件权限为 `0700/0600`，单文件 5 MiB
轮转，默认保留 30 天，并通过本地 `.git/info/exclude` 的 `/state/` 排除在 KB Git 之外。
