# byteworker · dashboard

> 仅在查看、刷新或维护工作看板时加载。任何写入走 `kb-mutation.md`。

`dashboard.md` 是实时视图：用户维护的“长期关注/需要关注”手动项是真相源，其余内容可重算。

刷新：

1. 长期关注：从绑定节点拉最新 TL;DR/状态；自由文本原样保留。
2. 需要关注：有限扫描非 reading 节点的 stale/90 天未验证状态、未裁决冲突，以及 Todo
   逾期/临期数量和最多 3 个标题。
3. 今日进展：从当天 journal 渲染，不独立保存历史。
4. 更新最后刷新时间。派生事实按 `citations.md` 给出来源；手动项、Todo、context 单列为本地状态。

写入：

- 新增/删除长期关注或手动提醒，只修改对应固定章节。
- 刷新完整看板时生成完整候选，并用 `replace_preserving_sections` 保留用户手动章节。
- 今日进展先通过 mutation 写 journal 对应事实，再刷新派生视图。
- 通过 `kb-mutate validate/execute` 写入；收到 commit receipt 前不得宣称完成。
- 带明确时间的一次性提醒走 Todo；长期/一次性无法判断时询问一次。
