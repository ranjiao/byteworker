# TODOS — byteworker 知识库 Skill

CEO 评审延后项(2026-05-20)。这些不在首版 skill 范围,但已记录上下文供后续拾起。

## P2 — lark-event 自动摄取
- **What:** 订阅飞书事件流,新会议纪要/文档自动进 raw_data/ 待消化队列。
- **Why:** 消除摄取摩擦——PKM 系统的真正瓶颈。首版是用户触发的拉取式。
- **Cons:** 后台长驻订阅、去重、待消化队列状态管理,复杂度明显高于首版其余部分。
- **Effort:** L(人)→ M(CC)。 **Depends on:** 首版摄取管线 + INDEX 稳定后再做。

## P2 — 周/月知识回顾(digest of digests)
- **What:** 自动汇总一段时间 journal/ 的新增,生成"本周你学到/决定了什么"。
- **Why:** 把分散的 digest 升华成阶段性认知;复用 lark-workflow-meeting-summary 的模式。
- **Effort:** S(人)→ S(CC)。 **Depends on:** journal/ 写入稳定。

## P2 — raw_data/ 归档策略 + INDEX 分片
- **What:** raw_data/ 无限增长治理;INDEX.md 行数 >200 时按 topic 域拆子索引。
- **Why:** 规模治理。首版已埋 >200 行硬触发提示,但分片逻辑本身延后。
- **Effort:** M(人)→ S(CC)。 **Depends on:** 知识库实际达到该规模。

## P3 — 行级溯源锚点
- **What:** 每个 digest 事实带 raw_data 行级锚点(首版为文件级溯源)。
- **Why:** 用户一键核对具体出处。文件级溯源已能满足首版可信度需求。
- **Effort:** M(人)→ S(CC)。

## P3 — 知识图谱可视化
- **What:** 用 lark-whiteboard 把 knowledge/ 的双链关系导出成图。
- **Why:** 直观看知识结构。锦上添花,非核心闭环。
- **Effort:** S(人)→ S(CC)。
