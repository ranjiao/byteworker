# Byteworker Dreaming Runner

这是用户明确启用的本地 Dreaming 定时任务。不要改变 Dreaming 设置，也不要处理未授权来源。

1. 在 byteworker skill 仓库运行一次 `bin/byteworker preflight`。
2. 从 `references/workflow-routes.json` 解析 `dreaming` workflow，并读取其完整 required 闭包。
3. 调用 `bin/byteworker dreaming run-due --kb "<KB>" --owner "<TASK_ID>"`。
4. `disabled/idle/busy` 时安静结束。
5. `leased` 时只执行返回的一个 `job/period`，遵守 Dreaming reference 对该 job 的要求；
   `job=maintenance` 时额外加载 workflow manifest 的 `features.maintenance`。
6. 所有来源操作只调用 `bin/byteworker` 公开命令；禁止直接 import digest/query 内部模块。
7. 成功、部分或失败路径都调用 `dreaming complete`；`partial/failed` 提供稳定错误码。
8. 不循环领取下一 job，不创建飞书任务，不 push。除 maintenance 按规则提交有限 doctor
   决策摘要外，不发送外部消息；不得在通知中包含业务正文。

启用提示已经由交互设置流程确认；runner 不重复打扰用户。机器从休眠恢复后，period 和 recovery
由 `run-due` 判定，runner 不自行补算。
