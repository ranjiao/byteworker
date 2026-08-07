# Dreaming Report Consumers

仅由 Dreaming report owner 运行。

## Coverage 与 Packet

窗口未覆盖时 `run-due` 返回 process catch-up lease；commit 后用该 run_id 调一次
`run-due --followup-after-run-id`，只领被本轮解锁的 morning/daily/weekly；否则 idle。

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
bin/byteworker dreaming report complete \
  --kb "<KB>" --token "<lease.token>" --input "<report.json>" \
  --item-count "<n>" --finding-count "<n>" --gap-count "<n>"
```

`report complete` 是报告 job 的唯一成功出口；它会确定性生成 manifest、私有产物、
`reports/<kind>/<period>.md` 归档快照，完成 Dreaming lease，并按配置处理投递：

- `report.json`：唯一语义结果。
- `summary.txt`：所有宿主回显的用户消息摘要。
- `report.md`：Agent 内部记录和引用审计。
- `report.html`：详细自包含页面；宿主预览或返回本地链接。
- `reports/<kind>/<period>.md`：用户可编辑归档快照，重跑时保留“手动补充 / 备注”。

HTML 禁止宿主私有 API 和外部脚本、样式、字体、图片或网络资源。TraeWork、Codex、Claude
Code 只消费 manifest。
4. 飞书固定用应用机器人发送 summary；仅当 `report_delivery.lark_bot.enabled=true` 且已有
   `ou_` 收件人时，`report complete` 才自动创建 outbox 并投递。投递命令固定为
   `bin/byteworker lark im +messages-send --as bot --user-id <ou_...> --text <格式化摘要>
   --idempotency-key <outbox_id>`；不要给 `lark auth status` 传 `--as`。发送前必须把
   `summary.txt` 转成可读文本消息，至少包含报告标题、项目符号摘要和完整报告路径，不得直接发送
   一整段未分组长文本。`message_id` 是送达回执。投递失败时 outbox 保持 pending，报告 job 仍可
   成功，因为本地产物已经落地；不得猜收件人，无回执只能说已生成。

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
