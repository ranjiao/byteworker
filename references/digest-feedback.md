# byteworker · Digest 反馈

## 反馈一次 Digest 运行

用户说“反馈刚才的摄取问题”时，沿用当前会话中该次 Digest 的 `run_id`。失败和已完成但结果
有误的运行都可反馈。若没有明确指向，先用 `digest-run list --limit 5` 查看近期运行。
只有一个候选时展示所选编号；有多个候选且无法从会话确定时，请用户按时间和阶段选择。
已知编号但日志不在保留窗口内时，仍沿用该编号并展示缺口。

```bash
bin/byteworker doctor feedback --run-id "<RUN_ID>"
bin/byteworker doctor bundle --run-id "<RUN_ID>"
```

两条命令都可用 `--kb` 指定知识库。反馈草稿的 `data` 给出 `to`、`subject`、`body` 和
`mailto`。展示收件地址、主题和正文供用户复制，宿主支持时提供“打开邮件草稿”链接。
预期和实际留空，供用户自行补写可对外分享的技术描述。Agent 直接展示工具产出的草稿，
不能补入对话、知识库内容或用户的内部工作信息。用户在自己的邮件工具检查并发送。
无人值守任务只保留正常 Digest 日志。

默认收件地址为 `ranjiao@gmail.com`。在运行 Byteworker 的终端或宿主环境设置
`BYTEWORKER_FEEDBACK_EMAIL` 即可覆盖，值为单个邮箱地址。

```bash
export BYTEWORKER_FEEDBACK_EMAIL="support@example.com"
```

只有用户明确要求诊断包时才运行 `doctor bundle`。返回本地 ZIP 路径、文件清单和可预览摘要。
用户可以检查后自行附到邮件。ZIP 以 `0600` 写在知识库和 skill 仓库之外的系统临时目录。
`summary.json` 包含版本、运行状态、事务结果、运行环境和诊断缺口；
`timeline.jsonl` 包含所选运行的时间、阶段、状态、耗时及固定原因码。
缺失、损坏或截断的日志显式标注，未知事务结果保持 `unknown`。

导出只从运行日志投影明确允许的技术字段。不读取知识库正文、缓存、候选、来源配置或
`doctor scan` 报告。即使运行日志含有来源类型、身份哈希或计数，这些也不能进入草稿或 ZIP。
未知原因码不导出，禁止拼接原始错误输出、日志行、路径、标题、URL、凭据或聊天上下文。

## 其他入口与 Dreaming

普通操作按 [前台运行记录](operation-trace.md) 得到 `BW-…`。用户没有给编号时可用
`doctor trace-list --kind <意图> --kb "<KB>"` 找候选，按时间与阶段确认所指请求。
Viewer 的看板和设置接口失败时也会返回可用于反馈的编号。

“为什么后台没跑”调用 `bin/byteworker dreaming diagnose --kb "<KB>"`，也可加
`--job morning`、`--tick-id <DT-…>` 或 `--run-id <DR-…>`。用唤醒记录、调度决定、租约和
终态解释情况。该入口只读，不能代替 `retry-job` 或 `run-due`。详情见 [Dreaming 诊断](dreaming.md#运行日志)。

`doctor feedback` 和 `doctor bundle` 接受上述三类编号。Dreaming 反馈需要指定同一 KB；
普通操作可以从已知编号打开。Viewer 的 Dreaming 调试页可以预览邮件草稿及下载 ZIP。
日志中的宿主任务标识、token、业务产物路径、批次标识、正文、报告和人员信息一律留在本地。
