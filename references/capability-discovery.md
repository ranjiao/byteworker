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

仅在有真人参与的交互 session 中，用户请求成功完成且没有未解决错误或用户裁决时调用：

```bash
bin/byteworker discover recommend --kb "<KB>" --after <capability>
```

Agent 可以基于当前对话添加有限语义信号：同一来源确实反复处理时加
`--signal repeated_source`；用户明确形成了自己的观点、假设或推演时加
`--signal user_judgment`。Python 只消费枚举信号，不解析用户正文。

返回 `suggestion` 时，把 `message` 和 `example` 合成一段简短收尾，最多一条，不使用独立大标题，
不覆盖主任务结果。`suggestion=null` 时保持静默。`recommend` 已原子记录本次成功使用和提示展示，
不要重复调用或再记录 `shown`。

## preflight 低频提示

`CAPABILITY_SUGGESTION` 只会在没有其它 notice、没有 blocking 且本地里程碑满足时出现。先完成
用户当前请求；只有该请求成功时才展示 `data.suggestion`。展示后调用：

```bash
bin/byteworker discover feedback --kb "<KB>" --capability <id> --action shown
```

如果当前请求失败或需要用户裁决，不展示也不记录，下一次仍可重新判断。

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
- 定时任务、runner、自动报告和其它无人值守 session 不调用 recommend、不展示 Tip；preflight
  默认禁用建议，只有明确的 `--interactive` 才允许返回 `CAPABILITY_SUGGESTION`。
- 状态只含能力 ID、次数和时间，位于 KB 私有且 Git 排除的 `state/`；不保存用户正文或来源 URL。
- 发现功能损坏、关闭或状态无效时不得阻塞 digest/search/update 等核心能力。
- `dreaming` 的 `proactive_policy` 固定为 `explicit_only`：只有用户表达持续监控、主动提醒或定期摘要
  意图时，才进入既有完整导览和授权流程；普通 Tip 和 preflight 绝不推荐或启用它。
