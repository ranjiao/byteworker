# Dreaming Actions

模型只能提出 `byteworker-action-plan/v1`，确定性 policy 和 Ledger 决定是否允许执行。

## 有限动作

| kind | 门禁 |
|---|---|
| `suppress` / `wait` | 自动允许，不产生外部写入 |
| `include_report` | 需要 `persist_report` grant，target 为 morning/daily/weekly |
| `instant_alert` | 默认关闭，需要 `instant_alert` grant |
| `todo_candidate` | 必须用户确认 |
| `source_candidate` | 必须用户确认 |
| `conflict_review` | 必须用户确认 |
| `knowledge_candidate` | 需要 archive grant、complete monitored evidence，并强制 recapture |

设置 action grants：

```bash
bin/byteworker dreaming grant set-actions --kb "<KB>" \
  [--persist-report] [--archive] [--instant-alert]
```

## 执行协议

```bash
bin/byteworker dreaming action plan --kb "<KB>" \
  --input "<ActionPlan.json>" --lease-token "<lease.token>"

bin/byteworker dreaming action claim --kb "<KB>" \
  --action-id "<A-id>" --lease-token "<lease.token>" [--confirmed]

bin/byteworker dreaming action validate-claim --kb "<KB>" \
  --action-id "<A-id>" --claim-token "<claim.token>"

# 通过现有公开 mutation/Todo/DigestTxn 执行动作，必须使用 claim 返回的 dedupe_key。

bin/byteworker dreaming action complete --kb "<KB>" \
  --action-id "<A-id>" --claim-token "<claim.token>" \
  --receipt "<downstream-receipt.json>"
```

下游 receipt 的 `idempotency_key` 必须与 action dedupe key 完全一致。报告/Todo/知识动作只接受
`status=committed`，Ledger 会验证 commit 确实存在于 KB Git 且变更路径匹配 action kind；
即时提醒要求 `delivery_id`；无写动作只接受 `status=noop`。Ledger 只保存 receipt hash、status、
commit 和 key，不复制业务正文。

需要确认的 action 没有 `--confirmed` 时 fail closed。grant revision、lease token 或 epoch 变化后，
旧 claim 不得执行新下游写入。

## 失败与恢复

- 未 claim 的 action 可 `action cancel --reason ...`。
- 已 claim action 不能直接取消。租约过期、进程崩溃或 grant 变化后进入：

```bash
bin/byteworker dreaming action reconcile --kb "<KB>" --action-id "<A-id>"
```

- 没有真实下游 receipt 时保持 `reconcile`，不得重新 claim。
- 找到真实 receipt 后用 `--receipt` 对账为 committed。
- committed action 重放相同 receipt 幂等；不同 receipt 拒绝。
- Action Ledger 不直接调用 provider，不 import digest/query core。
