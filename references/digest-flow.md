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
coverage 规则仍按来源 reference 与 `digest-concurrency.md`。

## 3. 一次准备到语义闸门

按 `source bundle-spec` 契约把只含 artifact 路径的 request 写入 `work_dir`，然后运行：

```bash
bin/byteworker digest-flow prepare --kb "<KB>" --run-id "<RUN_ID>" \
  --bundle-request "<WORK_DIR>/bundle-request.json"
```

命令依次完成 Bundle、preflight、analysis packet、dependency/semantic planner，并自动记录确定性阶段。
幂等命中直接终结为 noop；否则只消费返回的 `next_action`：`dependency_review` 或
`semantic_analysis`。artifact 路径可用 `digest-flow status` 恢复，不重跑已完成抓取。
需要向用户询问依赖范围或冲突时，按 `digest-observability.md` 先记录 `digest-run wait`；答复后
`resume`。等待不是失败，且不计入 active duration。

## 4. 提交与恢复

Agent 完成依赖、语义、冲突与候选计划后，把 `digest-plan/v2` 放入 `work_dir`：

```bash
bin/byteworker digest-flow commit --kb "<KB>" --run-id "<RUN_ID>" \
  --plan "<WORK_DIR>/digest-plan.json"
bin/byteworker digest-flow status --kb "<KB>" --run-id "<RUN_ID>"
```

`commit` 自动记录 transaction 并写 committed/noop 终态；失败保持 `commit_failed` 供修复后重试。
flow state 只保存 source ref hash、固定 phase 和私有 artifact 路径，不保存正文、标题、URL、凭据或
语义候选。usage 仍由宿主按 `digest-observability.md` 单独上报。
