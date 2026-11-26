# Dreaming Report Consumers

仅由 Dreaming report owner 运行。

## Coverage 与 Packet

调度按知识库时区处理今天和昨天。到期的近期日报有完整覆盖就先生成。两天都到期时先检查今天，
今天仍缺资料而昨天已覆盖时先出昨天。未到出报时间的今天继续摄取，工作日与原出报时间照常生效。
近期报告或定期摄取仍待完成时，周报、较早的日报与旧消息补跑排在后面。补采退避期间可做维护。
晨报仅在本地工作日的计划时间到 12:00 前可生成和发送。到 12:00 仍未完成即跳过，补采后的续跑与旧投递重试也遵守截止时间。
已有归档跳过。近期完成后再处理接管后缺失的工作日日报和周报，跨日、跨周保留欠账。自动 IM 仍遵守 14 天回看范围，超龄缺口保留。
当日领取的日报覆盖至计划出报时间，跨日补跑覆盖完整自然日。prepare 复用领取时的窗口。
日报补采每批提交后重查消息进度，直至报告窗口内的缺口全部补齐。定期来源完成后仍须核验消息。
补采成功后立即用其 run_id 调 `run-due --followup-after-run-id`。若该日报又出现缺口，继续处理；覆盖达标后同轮生成该日报。

报告 lease 到手后：

```bash
bin/byteworker dreaming report prepare \
  --kb "<KB>" --kind morning --period "<period>"
```

日报或周报将 `--kind` 改成 `daily` 或 `weekly`。

prepare 只读 committed Finding 和 durable KB 引用，不读 spool 或重跑 digest，返回 packet、
Finding 数量和 coverage：

- monitored cursor/gap 未完成：blocked。
- 任一定期 digest 来源没有与当前 Profile revision 一致、且覆盖窗口结束时间的成功 checkpoint：
  blocked；`run-due` 先调度 process catch-up。
- all_visible discovery：最多 partial，披露 best-effort。
- 来源 Profile 非法：迁移检查 fail closed；不存在“忽略某类 provider 后生成完整报告”的降级。

## 生成与提交

1. 读取 packet、report context 和有限 KB query。只报告有来源的业务内容，不把节点 updated、
   raw 数量或抓取 changed 数量当作进展；相同事实跨批次去重。覆盖/失败说明放一次独立的
   限制说明，不混进重点，不把报告运行摘要反向写成 knowledge 节点。
   未识别出周会周期的完整快照，其本轮已核验的信息增量可作为当期材料。事实发生时间以原文为准。
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

报告类型、周期和活跃租约须一致才会写归档。`report render --kb <KB> --input <report.json>`
只生成系统临时目录中的私有预览。`report delivery-complete` 只确认宿主渠道回执。

HTML 禁止宿主私有 API 和外部脚本、样式、字体、图片或网络资源。宿主按 manifest 选择产物。
4. 飞书固定用应用机器人发送 summary；配置入口可接受字母用户名，但必须先通过 user 身份通讯录
   唯一解析并把对应 open_id 保存到 `report_delivery.lark_bot.recipient_id`。仅当
   `report_delivery.lark_bot.enabled=true` 且已有 `ou_` 收件人时，`report complete` 才自动创建
   outbox 并投递。投递命令固定为
   `bin/byteworker lark im +messages-send --as bot --user-id <ou_...> --text <格式化摘要>
   --idempotency-key <outbox_id>`；不要给 `lark auth status` 传 `--as`。发送前必须把
   `summary.txt` 转成可读文本消息，至少包含报告标题、项目符号摘要和完整报告路径，不得直接发送
   一整段未分组长文本。`message_id` 是送达回执。投递失败时 outbox 保持 pending，报告 job 仍可
   成功，因为本地产物已经落地；不得猜收件人，无回执只能说已生成。

已确认收件人后，可用 `dreaming configure --kb <KB> --lark-delivery-enabled true --lark-recipient <用户名或open_id>`
保存配置。`--lark-recipient-id` 是兼容别名。已入队但投递失败时，维护者可用
`dreaming report deliver --kb <KB> --outbox-id <ID>` 重试原条目。`report enqueue-delivery` 属底层维护入口。

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
