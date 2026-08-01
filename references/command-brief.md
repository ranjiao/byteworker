# byteworker · brief

> 仅在生成会前简报时加载；查询细则复用 `command-search.md`，引用复用 `citations.md`。

1. 用 `lark-calendar` 的 agenda 能力取得日程；失败时明确告知，不静默猜会议。
2. 对每场会议提取主题与参会人，按 `command-search.md` 做有限知识召回和一跳图遍历。
3. 每场输出相关 project、decision、person 的 TL;DR、待确认风险和上下文。无命中时明确写
   “该会议在库中无相关上下文”。
4. 每个知识库事实就近标 `[S<n>]`，按 `citations.md` 展开原始出处、原文时间/覆盖、
   收录时间和置信度。
5. 这是用户触发的只读流程，不创建报告文件，不做后台推送。
