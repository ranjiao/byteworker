# Dreaming Consolidation

Finding history 是短期 Dreaming 运行状态，不是长期事实真相源。

- `finding-history.jsonl` 是每批 proposal delta 的可恢复事件日志；`findings.json` 是当前投影，
  可由剩余 delta 重建。
- 稳定事件键为 `batch_id + finding_id`。同一 batch 重放不追加重复事件。
- 同一 `finding_id` 跨 batch 更新时 revision 递增，evidence refs 和 batch ids 合并。
- 当前 lifecycle 为 `open/snoozed/resolved/dismissed/promoted`；模型只创建/更新 open Finding，
  生命周期反馈必须通过 `dreaming feedback` 和稳定 request id。
- history 先 fsync 追加，随后原子替换 projection。中途崩溃后从 history 重建，不重复展示。
- `persist_finding=false` 时只写 analysis/consolidation receipt 并推进 batch，不写 history/projection。
- grant 降级或关闭时，删除被撤销 batch 的未晋升 Finding 事件并重建投影；保留不含正文的最小
  batch 审计状态。
- Finding 和报告不能作为知识证据。需要长期知识时必须重新 capture SourceBundle 并走 DigestTxn。

Agent 不直接编辑 `finding-history.jsonl` 或 `findings.json`。
