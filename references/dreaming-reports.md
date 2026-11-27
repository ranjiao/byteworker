# Dreaming Report Consumers

仅由 Dreaming report owner 运行。

## Coverage 与 Packet

自动日报按知识库时区在工作日到点后优先领取。今天和昨天都待生成时先出今天，再出昨天。
宿主须在出报时间唤醒才能到点领取。如果宿主晚唤醒，或另一个任务仍持有有效租约，在可领取的下一次唤醒出报。
消息或定期来源有缺口时，使用已核实的信息生成日报，标注覆盖不完整。知识库回滚失败、来源配置损坏和归档错误会阻止相关日报发布。
近期报告或定期摄取仍待完成时，周报、较早的日报与旧消息补跑排在后面。
晨报仅在本地工作日的计划时间到 12:00 前可生成和发送。到 12:00 仍未完成即跳过，补采后的续跑与旧投递重试也遵守截止时间。
已有正式归档的周期跳过。基础 Markdown 发布后，同一期仍可领取正式日报。
近期完成后再处理接管后缺失的工作日日报和周报，跨日、跨周保留欠账。自动 IM 仍遵守 14 天回看范围，超龄缺口保留。
当日领取的日报覆盖至计划出报时间，跨日补跑覆盖完整自然日。prepare 和 complete 复用领取时的窗口。
日报归档完成后，定期摄取继续按自己的调度推进，来源检查点和消息缺口保持真实。

### 到点独立发布

工作日的计划时间由宿主独立调用 `bin/byteworker dreaming report publish-due --kb "<KB>"`。
该入口在本机确定性生成 Markdown，使用已验收的消息 Finding 和 Git 中带原始时间与出处的归档事件。
它不读取未验收的 Finding、不访问 provider、不调用模型或 HTML 渲染，也不等待摄取租约。
普通 `dreaming tick` 在预检前唤起同一路径，作为漏唤醒后的恢复机会。
要在截止分钟发布，宿主必须支持独立触发并在该分钟启动命令。

独立发布先保存私有版本和待发布回执，再原子写入日报归档路径。中断后按同一回执重放。
`diagnose --job daily` 显示最后的独立发布与近期事故。截止分钟后恢复的初版保留迟报记录。
`publish-due --supplement` 根据后续验收结果更新最近 14 天由该入口发布的日报，保留手动备注和历史版本。
若报告正文已由用户或其它报告流程另行编辑，自动补充保留该正文。正式日报归档时保留手动备注，并生成摘要和 HTML。
只有正式完成才清除自动正式报告待办。旧版基础发布提前清除的待办可按基础回执恢复。
启用飞书摘要时，正式成功回执与待投递条目一起写入状态。机器人随后在锁外发送。
中断或失败后可用原 outbox ID 重试，沿用同一投递幂等键。

报告 lease 到手后：

```bash
bin/byteworker dreaming report prepare \
  --kb "<KB>" --kind morning --period "<period>"
```

日报或周报将 `--kind` 改成 `daily` 或 `weekly`。

用户明确要求补指定周期时，可用 `dreaming report start --kb <KB> --kind daily
--period <日期> --owner <当前宿主>` 领取该周期，再 prepare / complete。
若用户明确要求将某来源的周期缺口写入日报而照常出报，逐项传
`--accept-source-gap <source_key>` 和 `--gap-note <限制说明>`；只允许接受当前周期已知的
定期来源缺口，不接受消息缺口或非法 Profile。来源 checkpoint 和 process 成功状态保持真实。
prepare 将这些缺口列入 partial_reasons，complete 强制 partial 和限制说明，发布前复核覆盖。
手动补报默认仅生成本地正式产物；已有明确发送授权时才传 `--deliver`。
此入口不创建定时任务、不修改业务时间安排，也不会因已有 Markdown 快照而跳过正式归档。

prepare 只读 committed Finding 和 durable KB 引用，不读 spool 或重跑 digest，返回 packet、
Finding 数量和 coverage：

- 消息游标落后、窗口中仍有消息缺口或定期来源检查点未覆盖期末：自动日报以
  `daily_coverage_gap` 记录在 `partial_reasons`。晨报、周报继续等待覆盖，手动补报按上面的明确授权执行。
- all_visible discovery 最多为 partial，报告披露消息发现仅尽力覆盖。
- 来源 Profile 非法时停止发布，保留错误以便诊断。

## 生成与提交

1. 读取 packet、report context 和有限 KB query。只报告有来源的业务内容，不把节点 updated、
   raw 数量或抓取 changed 数量当作进展；相同事实跨批次去重。覆盖/失败说明放一次独立的
   限制说明，不混进重点，不把报告运行摘要反向写成 knowledge 节点。
   未识别出周会周期的完整快照，其本轮已核验的信息增量可作为当期材料。事实发生时间以原文为准。
2. 一次生成 `byteworker-report-document/v1`；事实按 `references/citations.md` 回到原始
   evidence。日报 `message_summary` 最多 500 字，可核实内容少时简短说明。晨报和周报摘要为 300–500 字。
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
