# byteworker · digest 确定性编排

标准 digest 的无语义步骤统一走 `digest-flow`；不要再手工为 classify/capture/bundle/preflight/
analysis_prepare/transaction 成对调用 `digest-run stage`。语义判断仍由 Agent 完成，事务成功仍只认
`status=committed`。

## 1. 开始与私有工作目录

```bash
bin/byteworker digest-flow start --kb "<KB>" \
  --source-type "<SOURCE_TYPE>" --source-ref "<NON_SECRET_STABLE_REF>"
```

返回唯一 `run_id` 和 KB `state/digest/flows/<run_id>/` 私有 `work_dir`。后续 capture request、bundle
request、worker result、DigestPlan 都放该目录；不得放 skill 仓库。`start` 自动完成 classify 日志。

## 2. Capture 可多波执行

把 allowlisted `byteworker-digest-capture-plan/v1` 放进 `work_dir`，正文/评论一波、发现白板后可再跑
一波：

```bash
bin/byteworker digest-flow capture --kb "<KB>" --run-id "<RUN_ID>" \
  --request "<WORK_DIR>/capture.json"
```

命令自动记录 capture started/completed|failed；失败保持可恢复状态，不用部分结果继续。顺序分页和
coverage 规则仍按来源 reference 与 `digest-inline.md`；worker 并行时再加 parallel_execution。

## 3. 一次准备到语义闸门

按 `source bundle-spec` 契约把只含 artifact 路径的 request 写入 `work_dir`，然后运行：

```bash
bin/byteworker digest-flow prepare --kb "<KB>" --run-id "<RUN_ID>" \
  --bundle-request "<WORK_DIR>/bundle-request.json"
```

命令依次完成 Bundle、preflight、analysis packet、dependency/semantic planner，并自动记录确定性阶段。
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
旧运行缺少绑定时重新 prepare。`commit_failed` 可修复后重试。
事务成功但日志失败时，以事务回执为准，回执附 `digest_run_logging=degraded`。
私有状态保存 hash、阶段和产物路径。usage 和恢复见 `digest-observability.md`。
