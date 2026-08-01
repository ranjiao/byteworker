# byteworker · 语义判定锚点

> 本文件约束会改变持久化结果的 Agent 判断。措辞可以变化，reason code、最低证据和阈值不能
> 自由发挥。

## 知识晋升

结构化视图的普通记录只留 raw/provenance。满足至少一个 reason 才可晋升实体图：

| reason code | 最低证据 | 可晋升 |
|---|---|---|
| `explicit_decision` | 原文明确“决定/确认采用/生效”，有 anchor | decision，及受影响实体更新 |
| `dated_status_change` | 同一稳定记录出现带时间的新旧状态 | project/event 进展 |
| `time_bounded_event` | 有明确发生时间和参与对象 | event |
| `cross_record_theme` | 至少 2 条独立稳定记录共同指向同一长期主题 | area/project |
| `long_running_project` | 有 owner/目标/持续状态中的至少两项，且不是单次任务 | project |

不满足时不得因标题看起来重要而建节点。类型仍不确定时给用户最多 3 个候选及依据，不用
area/event 兜底。

## 参与方与推断

- “关键参与方”仅包括：明确发言影响决策、被分配责任、拥有审批/否决权，或直接承担风险的人。
- 立场必须绑定发言/行为 anchor。
- “动机/利益”默认不持久化；只有直接自述，或至少两条独立可定位观察支持时才可写【推断】。
- 证据不足则省略，不用“证据有限”包装猜测。

## IM 评分

`importance` 与 `relevance_to_user` 均为 0..4 整数：

| 分数 | importance | relevance |
|---:|---|---|
| 0 | 噪声/寒暄 | 无关 |
| 1 | 一般信息 | 仅外围相关 |
| 2 | 有用同步 | 与用户团队/项目相关 |
| 3 | 实质变化、动作或风险 | 用户职责直接相关 |
| 4 | 生效决策、严重风险或重大变化 | 明确指派用户/必须由用户处理 |

- `should_include_report = importance >= 3 and relevance_to_user >= 2`
- `should_digest_kb` 还要求 reason code 属于
  `explicit_decision/project_status_change/key_risk/cross_team_alignment`
- 每条必须带 `reason_codes` 和至少一组 chat/window/message_ids。
- 模型输出先运行 `bin/byteworker semantic validate-im --input <json>`；验证失败不得写报告或触发
  digest。
