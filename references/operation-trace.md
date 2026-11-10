# byteworker · 前台运行记录

用户开始 search、brief、update、thinking、context、dashboard 或手动报告时，
知道知识库位置后先调用 `bin/byteworker doctor trace-start --kind <意图> --kb "<KB>"`。
报告使用 `report`。这是技术运行编号，不是知识库记录或操作授权。保留返回的 `data.run_id`，
给本次相关的 `kb-query`、`kb-mutate`、`context`、`dashboard`、
`report-automation` 等命令传入同一个 `BYTEWORKER_TRACE_ID` 环境变量：

```bash
BYTEWORKER_TRACE_ID="<RUN_ID>" bin/byteworker kb-query search --kb "<KB>" --query "<QUERY>"
bin/byteworker doctor trace-finish --run-id "<RUN_ID>" --result success
```

最终回复前记录 Agent 已知的结果。枚举为 `success/failed/partial/no_match/insufficient_evidence/incorrect_result`。
仅当用户明确指出结果有误并能确定是同一请求时，可以为该编号补 `incorrect_result`。工具成功不证明回答正确。
没有启动记录、半途中断或旧历史不可追溯时如实说明。写入结果以事务回执为准，失败不推断回滚成功。
技术记录失败不影响本次业务行为。正常回复只在用户排障或需要反馈时展示编号。

来源、兼容检查和安装排障也可用对应 `source/doctor/install` 诊断类型。首次启动若尚未定位 KB，
`trace-start --kind install` 可不传 `--kb`，并把返回编号传给 `preflight`。
Python 或宿主在进入 CLI 前失败时查宿主本地任务历史，无法凭缺少记录判断原因。
