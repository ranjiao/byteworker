# Dreaming Foreground、Review 与 Shadow

## Foreground Process

Dreaming disabled 时，用户仍可显式执行一次 IM 处理：

```bash
bin/byteworker dreaming process once --kb "<KB>" --source im \
  --mode monitored --start "<ISO>" --end "<ISO>"

bin/byteworker dreaming process once --kb "<KB>" --source im \
  --mode all_visible --acknowledge-all-visible \
  --start "<ISO>" --end "<ISO>"
```

该命令创建最长 2 小时的单次 authorization 并完成 prepare：

- 不修改 `enabled/jobs`，不创建宿主任务。
- 不修改或继承 persistent IM mode / `persist_finding`。
- all_visible 仍需本次显式确认。
- 返回 batch id，不返回 session token 或消息正文。
- Agent 按 `dreaming-analysis.md` 生成 FindingBundle，再调用普通 `process commit`。
- commit/abort 后 session 关闭；foreground Finding 不持久化，但 batch/cursor/gap 契约与后台一致。

## Review 与 Explain

```bash
bin/byteworker dreaming review --kb "<KB>" \
  --status open|snoozed|resolved|dismissed|promoted|all --limit 50

bin/byteworker dreaming explain --kb "<KB>" "<finding_id>"
```

review 只返回有限摘要，不返回 evidence refs 或 raw。explain 返回 Finding、manifest 路径、source、
window、coverage 和匹配 evidence ids，不返回 spool 正文。

反馈写入需要稳定 request id，重复 request 幂等：

```bash
bin/byteworker dreaming feedback --kb "<KB>" "<finding_id>" \
  --status dismissed --value unimportant --request-id "<stable-id>"

bin/byteworker dreaming feedback --kb "<KB>" "<finding_id>" \
  --status snoozed --value already_known --request-id "<stable-id>" \
  --snooze-until "<future ISO8601>"
```

feedback 可选值：`helpful/unimportant/already_known/handled/wrong_link/none`。没有交互不能自动生成负面
反馈。`promoted` 只记录 lifecycle；真实知识写入仍需 Action Ledger + DigestTxn。

## Shadow Evaluation

评估目录必须位于 KB 和 skill 仓库之外，包含：

- `golden.json`: `samples[]` 只允许 sample_id、priority、slices、expected。
- `legacy.json` / `dreaming.json`: `predictions[]` 只允许 sample_id、selected。

禁止在这些 JSON 中写消息正文、摘要、人员或项目内容。运行：

```bash
bin/byteworker dreaming shadow evaluate \
  --kb "<KB>" --evaluation-dir "<private-dir>"
```

输出和 `metrics.json` 只包含指标和 sample IDs；`metrics-history.jsonl` 同样位于私有评估目录。
单日门槛还要求 Golden Set 至少 200 个样本，决定/责任/风险/短回复/P2P/免打扰/低活跃/
附件不可读/partial coverage 九个切片各至少 20 个正样本。产品门槛要求最近 10 个工作日全部通过且跨度至少 11 天，才返回
`eligible_for_inbox_removal=true`。该字段只记录评估事实，不触发代码或路由变更；I7 已由用户
显式提前执行时也不得伪造该字段。
