# Byteworker Dreaming Runner

这是用户明确启用的本地 Dreaming 定时任务。不要改变 Dreaming 设置，也不要处理未授权来源。

1. 在 byteworker skill 仓库运行一次 `bin/byteworker preflight`。
2. 从 `references/workflow-routes.json` 解析 `dreaming` workflow，并读取其完整 required 闭包。
3. 调用 `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>"`。
4. `disabled/idle/busy` 时安静结束。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   `job=maintenance` 时额外加载 workflow manifest 的 `features.maintenance`。
6. 保存 lease 的 `run_id`。进入 collection/analysis/consolidation/action/report/maintenance/
   recovery 阶段时调用 `dreaming heartbeat`；单阶段超过 60 秒时至少再写一次 heartbeat。
   `detail-code` 只用有限机器码，禁止放业务正文。
7. 所有来源操作只调用 `bin/byteworker` 公开命令；禁止直接 import digest/query 内部模块。
8. 成功、部分或失败路径都调用 `dreaming complete`；`partial/failed` 提供稳定错误码。已知时传
   `--item-count/--finding-count/--gap-count`，不得用字符串 checkpoint 代替可结构化计数。
9. morning/daily/weekly 按 `references/dreaming-reports.md` 一次生成结构化报告并渲染
   summary、内部 Markdown 和自包含 HTML。任务结果必须回显 300-500 字 summary 和 HTML
   绝对路径；宿主支持本地产物预览时可直接预览，否则返回可点击文件链接。不得调用宿主私有
   HTML API。
10. 仅当 `dreaming status.report_delivery.lark_bot.enabled=true` 时，才把 summary 入队并由
    应用机器人投递给已配置收件人；保存真实消息回执。飞书失败不删除本地产物，也不得冒充送达。
11. 不循环领取下一 job，不创建飞书任务，不 push。除已配置的报告摘要和 maintenance 按规则
    提交的有限 doctor 决策摘要外，不发送外部消息。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
