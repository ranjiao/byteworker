# byteworker 能力发现

本流程回答“byteworker 还能帮我做什么”“根据我的知识库推荐下一步”，并约束成功任务后的低频
能力提示。面向用户只说目标、例句和必要设置，不要求理解内部命令、状态文件或 schema。

## 个性化能力地图

调用：

```bash
bin/byteworker discover status --kb "<KB>"
```

根据返回的 `capabilities`，优先展示 3-5 个与用户目标有关的能力。每项只包含：能解决什么、可以
直接怎么说、当前是 `available` / `used` / `dismissed`、是否需要授权或配置。用户要求完整列表时
再展示全部能力。

`references/capabilities.json` 是面向用户能力与推荐文案的唯一事实源。它不同于面向确定性工具的
`lib/command_registry.py`；不得把 `digest-txn`、SourceBundle、内部 namespace 等实现概念直接当成
用户能力。

## 成功任务后的上下文提示

主 Agent 必须遵守 `references/discovery-completion.md`：有真人参与的业务请求成功完成后，
在最终回复前调用一次，不先猜测是否需要推荐。失败、待裁决、worker 和无人值守不调用：

```bash
bin/byteworker discover complete --kb "<KB>" --interactive --after <capability>
```

Agent 可以基于当前对话添加有限语义信号：同一来源确实反复处理时加
`--signal repeated_source`；用户明确形成了自己的观点、假设或推演时加
`--signal user_judgment`。Python 只消费枚举信号，不解析用户正文。

返回 `suggestion` 时，把 `message` 和 `example` 合成一段简短收尾，最多一条，不使用独立大标题，
不覆盖主任务结果。`suggestion=null` 时保持静默。`complete` 已原子记录本次成功使用和提示展示，
不要重复调用或再记录 `shown`。
先选上下文规则，没有命中再选本地里程碑规则；update/context 也记录成功事件。主请求内的辅助
search/digest 等不分别记成功。`recommend` 是 complete 的兼容 CLI 名称，同样需要 --interactive。

## preflight 低频提示

`CAPABILITY_SUGGESTION` 在没有 blocking 且本地里程碑满足时出现，可与报告升级 notice
共存。它只是候选；完成当前业务请求后必须用 complete 重新选择，不直接展示旧候选或写 shown。

## 不推荐原因诊断

```bash
bin/byteworker discover diagnose --kb "<KB>" --interactive --after search
```

诊断只读当前快照，不创建状态文件或锁，不记成功或展示。省略 after 时检查启动里程碑；提供
after/signal 时检查对应当前条件，不模拟一次未来成功。返回 state_exists、successful_uses、
last_tip_at、gates 和逐规则 reasons。state_exists=false 表示缺少使用回执，不能据此断言没有用过。
原因码：unattended、task_failed、task_needs_input、tips_disabled、after_mismatch、signal_required、
insufficient_events、insufficient_facts、configuration_mismatch、already_used、dismissed、snoozed、
impression_limit、cooldown。eligible=true 表示当前可选，实际 complete 优先选择上下文规则中的
最高优先级，再选里程碑规则。状态损坏由结构化 error.code 返回，不静默伪装成冷却。

## 用户反馈

- “别再提示这个”：`feedback --capability <id> --action dismiss`
- “以后再说”：`feedback --capability <id> --action snooze --days 30`
- “关闭功能建议”：`feedback --action disable`
- “重新开启功能建议”：`feedback --action enable`
- 用户主动使用某项能力但本轮不需要推荐：`feedback --capability <id> --action used`

全局提示至少间隔 7 天或 5 次成功使用；同一能力最多展示两次。首次摄取后的查询提示属于核心
激活闭环，只展示一次且可绕过全局间隔，但仍受拒绝状态限制。

## 边界

- 推荐资格只读取本地状态、节点数量和报告设置，不为了提示访问飞书、日历、网络或发起 OAuth。
- 定时任务、runner、自动报告和其它无人值守 session 不调用 complete/recommend、不展示 Tip；preflight
  默认禁用建议，只有明确的 `--interactive` 才允许返回 `CAPABILITY_SUGGESTION`。
- 状态只含能力 ID、次数和时间，位于 KB 私有且 Git 排除的 `state/`；不保存用户正文或来源 URL。
- 发现功能损坏、关闭或状态无效时不得阻塞 digest/search/update 等核心能力。
- `dreaming` 的 `proactive_policy` 固定为 `explicit_only`：只有用户表达持续监控、主动提醒或定期摘要
  意图时，才进入既有完整导览和授权流程；普通 Tip 和 preflight 绝不推荐或启用它。
