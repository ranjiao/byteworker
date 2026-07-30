# byteworker · 自动日报 / 周报细则 —— 定期摄取 + 工作总结报告

> 由 harness 原生定时任务路由到这里；用户明确要求补生成 / 重跑某日或某周报告时也复用本流程。
> 执行前仍需完成「操作前必读」：自动更新、读 `.kbconfig`、读 `context.md`。

byteworker skill 负责报告方法，Codex / Claude / TRAE 等 harness 负责调度。每次运行先复查已登记
的 routine 来源并消化新增内容，再基于知识库生成日报 / 周报；设置与无人值守边界见
`references/report-scheduling.md`。不提供 `daily` / `weekly` 用户子命令；自然语言补跑只是
恢复入口，不是日常触发负担。

## 1. 共同流程

交互式补跑时先告诉用户会先跑定期摄取、再召回事实源、最后写报告和本地回滚点。自动任务只在
宿主运行记录中写有限元信息，不输出业务原文。

1. **获取执行租约**
   - 通过机器协议调用 `report-automation lease`，日报用 `kind=daily` 和 `YYYY-MM-DD`，
     周报用 `kind=weekly` 和 `YYYY-Www`。
   - `REPORT_AUTOMATION_BUSY` 表示另一个自动报告或人工补跑正在处理同一 KB；本次安全退出。
   - 取得 token 后必须在成功或失败路径调用 `report-automation complete`，租约意外遗留则等
     TTL 到期后恢复。
2. **先跑定期摄取**
   - 必读 `references/digest-routine.md`,按其中「运行」规则处理 INDEX「定期摄取清单」。
   - **自动日报每次都必须执行完整 routine digest**；自动周报和用户补跑也一样。这一步不受
     `.last-routine-digest` 是否到期或七天提醒阈值限制。
   - 只重放已登记且启用的 routine 来源；无人值守运行不得新增来源、扩大范围、发起 OAuth 或
     静默切换身份。授权 / 资源权限未就绪时 fail closed。
   - 即便没有增量,也要追加 journal,并把当天日期写入 `.last-routine-digest`。
3. **确定报告范围**
   - 自动日报：执行当天 00:00 到当前时刻；补跑给定 `YYYY-MM-DD` 时取该自然日完整范围。
   - 自动周报：上一完整 ISO 周；补跑给定 `YYYY-Www` 时取对应 ISO 周。
4. **召回事实源**
   - 读范围内 `journal/` 行。
   - 扫范围内新增/更新的 `raw_data/` frontmatter:按 `ingested`、`source_window`、`digest_period` 判断归属。先按 DESIGN.md §2.1 规范化时间:`ingested` / `source_window` 用完整 ISO8601,日期周期用 `YYYY-MM-DD`,ISO 周用 `YYYY-Www`。
   - 扫范围内新建/更新的 `knowledge/` 节点:优先读取 `event` / `decision`,再读取被它们 links 指向的 `project` / `person` / `org` / `area`。
   - 对用户本人、团队、直属主管方向、`dashboard.md` 长期关注项做一跳图遍历补充。
5. **筛选重要性**
   - 必纳入:明确决策、项目状态变化、关键指标明显变化、事故/风险/阻塞、跨团队协作变化、与你本人或团队直接相关的关键交互。
   - 可纳入:重要技术路线、资源 / 排期 / 人力变化、对下周有动作含义的讨论。
   - 排除:纯同步流水、重复摘要、寒暄、无留存价值的低信号消息。
6. **写报告**
   - 写入前确保 `reports/daily/` 与 `reports/weekly/` 目录存在;老知识库没有这些目录时直接创建。
   - `daily` 复制 `templates/report-daily.md` 的结构,写到 `reports/daily/<YYYY-MM-DD>.md`。
   - `weekly` 复制 `templates/report-weekly.md` 的结构,写到 `reports/weekly/<YYYY>-W<WW>.md`。
   - 报告是可覆盖快照:同一日期 / 周再次生成时可以覆盖原文件,但要保留用户已手动补充的 `## 手动补充 / 备注` 章节内容。
   - 条目按**事件发生时间倒序**排列;无法判断发生时间的条目放末尾并标"时间不明"。报告正文时间统一用 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`,不要输出 `20260520` / `5-21` / 裸 ISO 时间戳。
   - 每个事实性条目就近带 `[S<n>]`,并按 `references/citations.md` 沿节点 / journal / 既有报告
     追到原始 raw;「引用」章节列具体文档 / 妙记录屏 / 会议 / 群聊窗口、原文时间或覆盖范围、
     `ingested` 收录时间、版本与 raw_id。节点 id、raw_id 或 journal 日期只能作为库内线索,
     不能单独充当原始出处。不要写无来源结论。
   - 无命中章节写"暂无",不要编造。
   - 日报和周报最重要的章节是「本日重点 / 本周重点」,一定确保最重要的进展、重要人物观点、重要决策都明确录入,同时保证整体篇幅尽可能精简。
7. **写入收尾**
   - 向当天 journal 追加一行,说明生成了哪个报告、覆盖或新建、参考了哪些主要节点 / raw。
   - 在知识库数据目录本地 git 精确暂存本次改动路径后创建回滚点(`git commit`),永不 push。
   - 回显报告的本日 / 本周重点、最重要 3-7 条、风险 / 下步、报告文件路径;回显中的知识库事实
     同样保留 `[S<n>]` 和完整引用,不能因为报告文件已落盘就省略。
   - 报告文件与知识库本地 commit 真实完成后调用
     `report-automation complete --run-status success --report-path <相对路径>`。取得租约后的任何
     失败尽力调用 `complete --run-status failed --error-code <稳定错误码>`；宿主任务进程正常
     退出不等于报告成功。

## 2. 日报写作口径

日报回答:"今天发生了什么重要事,跟我和我的团队有什么关系,明天 / 接下来该看什么?"

### 可选:IM Inbox 扫描

普通自动日报默认不扫描用户全部 IM。若用户明确说「日报包含 IM」「分析今天聊天里最重要的事」
「最近一天 IM 重点」,则在「召回事实源」后加跑 `references/im-inbox-summary.md`:

- 运行前向用户发状态:即将扫描 IM 候选、做本地降噪、生成 `reports/im/` 摘要后再合入日报。
- 先用本地规则和预算限制筛选 IM,不要把全天聊天全量送入模型。
- 若脚本返回 `first_run_notice.shown=true`,先向用户说明 IM Inbox 的运行逻辑与存储边界,并提醒用户后续可补充 `--keyword` 或维护 `context.md` 里的关注项目 / 人 / 组织 / 业务词。
- 若脚本返回 `repeat_run_notice.shown=true`,提醒用户 IM Inbox 是重扫描命令,建议一天一次;短时间重复运行通常收益很低。提醒即可,不要阻断用户显式要求的重跑。
- 先把最终精判摘要保存到 `reports/im/`,再把高置信事项写入日报;不把全量 IM 原文入库。
- 日报可把 `reports/im/<...>.md` 记为“经由”,但「引用」章节仍按
  `references/citations.md` 展开 IM 的 chat / window / message_ids / 扫描时间;已提升为标准
  digest 的 thread 优先引用其 raw。
- 只有明确决策 / 项目状态变化 / 关键风险 / 重要跨团队对齐,才把对应 thread 按 `references/digest-chat.md` 重新拉小窗口并 digest 成标准 raw/event。
- 报告中必须写明扫描统计与截断情况,例如扫描会话数、原始消息数、候选 thread 数、是否超过预算。

建议输出顺序:
1. 本日重点:3-5 条,只写与你本人、你的团队、主管方向直接相关的事项。
2. 团队 / 项目进展:按项目聚合,每个项目不超过 3 条关键变化。
3. 决策与结论:当天新决策或重要对齐。
4. 风险 / 阻塞 / 需要跟进:明确责任人 / 下步若来源里有写。
5. 明日 / 后续关注:从风险、待办、未闭环讨论中归纳,标"建议"而非事实。
   另读 `todo.md` 中已确认且逾期 / 明日到期的 active 项,单列为“你的 Todo”,不要和报告建议混写。

## 3. 周报写作口径

周报回答:"这一周团队推进了什么,形成了哪些判断和决策,风险在哪里,下周抓什么?"

建议输出顺序:
1. 本周重点:5-8 条,按重要性而非时间排序,归纳 2-5 条主题线,不要只是日流水拼接。
2. 关键进展:按团队 / 项目聚合,保留指标变化和里程碑。
3. 决策与对齐:列出影响后续工作的决策、边界、资源约定。
4. 风险 / 阻塞:写清影响、当前状态、建议跟进对象。
5. 下周关注:可执行、可检查,区分"来源待办"与"报告建议"。
   `todo.md` 中已确认且下周到期的 active 项单列为“你的 Todo”,完成状态以 `todo.md` 为准。
6. 引用:按 `references/citations.md` 列实际支持正文的原始出处、原文时间 / 覆盖、
   收录时间与版本;节点 / raw / journal 仅作库内核对。

## 4. 与其它能力的边界

- `digest` 负责把外部资料消化成节点；自动日报 / 周报每次都在开头运行 routine digest，但不
  替代用户指定 URL 的深度摄取，也不授权新增 routine 来源。
- `dashboard` 是实时视图；日报 / 周报是归档快照。
- `context.md` 只读,用作判断"与你和团队相关"的透镜;报告中提到 context 推导时标为"你的视角"或"建议",不要当客观事实。
- `todo.md` 是用户行动状态源;报告只引用当前快照,不在报告正文里维护完成 / 延期状态。
- 报告不是知识节点,不进入 `INDEX.md`;但报告引用的事实必须能回到节点 / raw / journal。
