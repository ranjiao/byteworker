# 交互任务公共收尾

主 Agent 在真人请求成功完成、最终回复前必须执行一次，不预判是否有推荐：

```bash
bin/byteworker discover complete --kb "<KB>" --interactive --after <capability>
```

capability 使用 digest/search/update/brief/dashboard/context/thinking/routine/reports/viewer；
报告用 reports，来源登记用 routine。子任务和 worker 不重复调用。失败、等待裁决、无人值守、
help/discover/doctor/dreaming 跳过；无人值守不传 interactive，CLI 默认也不推荐、不写发现状态。

返回 suggestion 时在主结果后附 message 和 example，最多一条；null 保持静默。命令已经记录
使用与展示，不再写 shown；preflight 候选由本次选择替代，不另行展示。异常不影响业务结果。
只有当前对话确有证据时添加 --signal repeated_source 或 --signal user_judgment。
用户反馈和排障再读 `references/capability-discovery.md`。
