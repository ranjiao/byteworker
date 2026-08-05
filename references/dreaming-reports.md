# Dreaming Report Consumers

仅由 Dreaming report owner 运行。

## Coverage 与 Packet

窗口未覆盖时 `run-due` 返回 process catch-up lease；commit 后下一 tick 领取报告。

报告 lease 到手后：

```bash
bin/byteworker dreaming report prepare \
  --kb "<KB>" --kind morning|daily|weekly --period "<period>"
```

prepare 只读 committed Finding 和 durable KB 引用，不读 spool 或重跑 digest，返回 packet、
Finding 数量和 coverage：

- monitored cursor/gap 未完成：blocked。
- all_visible discovery：最多 partial，披露 best-effort。
- 不支持的 routine provider：partial，并禁止 daily/weekly 迁移。

## 生成与提交

1. 读取 packet、report context、Todo 和有限 KB query。
2. 一次生成 `byteworker-report-document/v1`；事实按 `references/citations.md` 回到原始
   evidence，`message_summary` 为 300-500 字。
3. 将候选保存到 KB 外临时文件，调用：

```bash
bin/byteworker dreaming report render --kb "<KB>" --input "<report.json>"
```

render 确定性生成 manifest 和四个私有文件：

- `report.json`：唯一语义结果。
- `summary.txt`：所有宿主回显的用户消息摘要。
- `report.md`：Agent 内部记录和引用审计。
- `report.html`：详细自包含页面；宿主预览或返回本地链接。

HTML 禁止宿主私有 API 和外部脚本、样式、字体、图片或网络资源。TraeWork、Codex、Claude
Code 只消费 manifest。
4. 按 `references/dreaming-actions.md` plan/claim/validate `include_report`。
5. 用 `kb-mutate execute` 写内部 Markdown，保留“手动补充 / 备注”。
6. 将真实 mutation receipt 包装为 action downstream receipt，含 `status=committed`、
   `idempotency_key=<claim.dedupe_key>` 和 `commit=<KB commit>`。
7. `action complete` 后才成功完成 Dreaming job。

生成与投递分别判定。需要投递时：

```bash
bin/byteworker dreaming report enqueue-delivery \
  --kb "<KB>" --kind "<kind>" --period "<period>" \
  --report-path "<reports/...>" --commit "<commit>" \
  --channel lark_bot --artifact summary --recipient-id "<ou_...>"

bin/byteworker dreaming report deliver \
  --kb "<KB>" --outbox-id "<OUT-id>"
```

飞书固定用应用机器人发送 summary；`message_id` 是送达回执。不得猜收件人；无回执只能说已生成。

## Owner Migration

先在宿主 UI 停止旧 daily/weekly/recovery 任务，再执行：

```bash
bin/byteworker report-automation release-owner \
  --kb "<KB>" --acknowledge-tasks-stopped

bin/byteworker dreaming manage-reports \
  --kb "<KB>" --enabled true --acknowledge-owner-released
```

保存 legacy snapshot 和 migration epoch；不支持的 provider 令迁移失败。

回滚：

1. `dreaming manage-reports --enabled false`
2. 在宿主 UI 按 snapshot 恢复旧任务
3. `report-automation restore-owner --acknowledge-tasks-restored`
