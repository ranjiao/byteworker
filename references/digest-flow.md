# byteworker · digest 确定性编排

`digest-flow` 编排确定性步骤并自动打点，Agent 负责语义判断；事务成功只认 `status=committed`。

## 1. 开始与私有工作目录

```bash
bin/byteworker digest-flow start --kb "<KB>" \
  --source-type "<SOURCE_TYPE>" --source-ref "<NON_SECRET_STABLE_REF>"
```

返回 `run_id` 和私有 `work_dir`。本轮 request、worker result、DigestPlan 均放该目录，不得放 skill 仓库。

## 2. Capture 可多波执行

把 allowlisted `byteworker-digest-capture-plan/v1` 放进 `work_dir`，正文/评论一波、发现白板后可再跑
一波：

```bash
bin/byteworker digest-flow capture --kb "<KB>" --run-id "<RUN_ID>" \
  --request "<WORK_DIR>/capture.json"
```

失败可恢复，不用部分结果继续。分页和 coverage 按来源 reference 与 `digest-inline.md`；worker 并行时加 parallel_execution。

## 3. 一次准备到语义闸门

按 `source bundle-spec` 契约把只含 artifact 路径的 request 写入 `work_dir`，然后运行：

```bash
bin/byteworker digest-flow prepare --kb "<KB>" --run-id "<RUN_ID>" \
  --bundle-request "<WORK_DIR>/bundle-request.json"
```

命令生成 Bundle、预检、分析包与分片计划。
群聊 Bundle 若证明分页完整且零消息，准备阶段直接返回 `phase=noop` 和
`receipt={status: empty_window, digest_run_id: …}`，`next_action=none`。不用分析或提交，
可用 `digest-flow status` 取回回执。
幂等命中返回 noop。按 `next_action` 继续，恢复时用 `digest-flow status` 取 artifact。
定期批量检查可加 `--check-only`，先做 Bundle 与预检，变化项进入 `checked`。已有 SourceBundle
时可用 `--bundle` 代替 request。随后只为变化项调用 `digest-flow analyze` 生成分析包和分片计划。
需要向用户询问依赖范围或冲突时，按 `digest-inline.md` 先记录 `digest-run wait`；答复后
`resume`。等待不是失败，且不计入 active duration。

## 4. 提交与恢复

Agent 完成依赖、语义、冲突与候选计划后，把 `digest-plan/v2` 放入 `work_dir`：

```bash
bin/byteworker digest-flow commit --kb "<KB>" --run-id "<RUN_ID>" \
  --plan "<WORK_DIR>/digest-plan.json"
bin/byteworker digest-flow status --kb "<KB>" --run-id "<RUN_ID>"
```

提交前完成分析准备。计划引用本轮 `work_dir/source-bundle.json`，Bundle 和组件须与预检一致。
旧运行缺少绑定时重新 prepare。`commit_failed` 修复候选后可重试。
需要结束失败尝试时用 `digest-flow fail --kb <KB> --run-id <ID> --error-code <CODE>`；
它关闭未结束阶段并保留产物。继续时用 `digest-flow resume --kb <KB> --run-id <ID>`，
按 next_action 重试 capture、prepare 或 commit。旧 Agent 已将日志标记 failed 的运行同样可恢复。
已成功或已取消的运行不可恢复。阶段耗时打点可选，不是提交前提。
已有来源无业务增量时可传 `knowledge_effect: snapshot_only` 和空 nodes，仅归档新版本。
事务成功但日志失败时，以事务回执为准，回执附 `digest_run_logging=degraded`。
私有状态保存 hash、阶段和产物路径。usage 和恢复见 `digest-observability.md`。
