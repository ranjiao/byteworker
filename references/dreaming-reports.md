# Dreaming Report Consumers

仅在 Dreaming report owner 生效后使用。旧 report automation owner 未释放时不得运行本流程。

## Coverage 与 Packet

`run-due` 发现报告窗口未覆盖时不会领取报告，而会返回独立 process catch-up lease，包含
`dependency.start/end/lane`。runner 用该窗口执行 process；成功 commit 后下一 tick 才领取报告。

报告 lease 到手后：

```bash
bin/byteworker dreaming report prepare \
  --kb "<KB>" --kind morning|daily|weekly --period "<period>"
```

prepare 只读取 committed Finding projection 和 durable KB 引用，不读 spool、不重新运行完整
routine digest。返回私密 packet path、Finding 数量和 coverage：

- monitored cursor/gap 未完成：blocked，不生成 packet。
- all_visible discovery：最多 partial，必须在报告中披露 best-effort。
- 尚未支持的 routine provider：partial；存在时禁止 daily/weekly owner migration。

## 生成与提交

1. 读取 packet、`context view --intent report`、Todo 和有限 KB query。
2. 使用 packet 指定模板生成完整候选；所有事实按 `references/citations.md` 回到原始 evidence。
3. 生成 `include_report` ActionPlan，按 `references/dreaming-actions.md` plan/claim/validate。
4. 用 `kb-mutate execute` 写报告，保留“手动补充 / 备注”。
5. 将真实 mutation receipt 包装为 action downstream receipt：
   - `status=committed`
   - `idempotency_key=<claim.dedupe_key>`
   - `commit=<KB commit>`
6. `action complete` 成功后，才 `dreaming complete --run-status success --artifact-path ...`。

报告生成成功和投递成功分离。需要投递时：

```bash
bin/byteworker dreaming report enqueue-delivery \
  --kb "<KB>" --kind "<kind>" --period "<period>" \
  --report-path "<reports/...>" --commit "<commit>"

bin/byteworker dreaming report delivery-complete \
  --kb "<KB>" --outbox-id "<OUT-id>" --delivery-id "<delivery-id>"
```

没有 delivery receipt 只能说报告已生成，不能声称用户已收到。

## Owner Migration

迁移前先在宿主 UI 停止旧 daily/weekly/recovery 任务，然后：

```bash
bin/byteworker report-automation release-owner \
  --kb "<KB>" --acknowledge-tasks-stopped

bin/byteworker dreaming manage-reports \
  --kb "<KB>" --enabled true --acknowledge-owner-released
```

Dreaming 会保存 legacy snapshot 和 migration epoch。若存在尚未支持的 routine provider，迁移
fail closed。

回滚顺序：

1. `dreaming manage-reports --enabled false`
2. 在宿主 UI 按 snapshot 恢复旧任务
3. `report-automation restore-owner --acknowledge-tasks-restored`

不能先恢复旧任务，否则同一 period 可能出现双 owner。
