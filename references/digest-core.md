# byteworker · digest 主流程

> 由 `SKILL.md`「digest」路由到这里。执行任何摄取前先读本文件,再按来源类型读取对应
> `references/digest-*.md` 细则。

## 触发

子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、或本地 md 路径,说"存入知识库""消化这个""记一下""把 XX 群最近的讨论存进来"。

**不带来源**的 `digest`、或"跑定期摄取""检查周报更新" → 运行定期摄取(见 `references/digest-routine.md`)。

## 主流程

1. **分类** —— 判定 `source_type`:`feishu_doc` / `feishu_minutes` / `feishu_meeting` / `feishu_chat` / `web` / `local_md`。**若输入是一整场会议**(日历会议链接 / 日程,或同属一场会的投屏文档 + 妙记多个 URL)→ 这是「会议簇」,整体摄取成一个 event,见下方场景细则。
2. **摄取原文**:
   - `feishu_doc` → 用 `lark-doc +fetch --api-version v2` 读取文档正文。**摄取前必读** `references/digest-doc.md`。
   - `feishu_minutes` → 优先用 `lark-vc` / `lark-minutes` 取纪要、AI 产物(总结/待办/章节)、逐字稿;若只有会议号/日程,先用 `lark-vc` 定位会议产物和 minute token。
   - `feishu_meeting` → 用 `lark-vc` 取会议纪要产物;拿到 minute token 后再取妙记正文 / AI 产物。
   - `feishu_chat` → 运行 `bin/pull-chat.sh` 拉取群聊(底层调 lark-im,自动定位群 + 分页拉全 + 输出逐字转写)。**摄取前必读** `references/digest-chat.md`。
   - `web` → 外部读物(blog/论文/wiki):用宿主 agent 的网页抓取/浏览能力取得正文,本地 PDF / 文章则读取本地文件。**摄取前必读** `references/digest-reading.md`。
   - `local_md` → 直接读取本地文件。
   失败按 `references/error-handling.md` 中止。
3. **落原文** —— 写 `raw_data/<YYYY-MM-DD>-<slug>.md`:逐字原文 + frontmatter(`digest_status: pending`)。**raw 正文一旦写入永不改写**;digest 完成 / 失败 / 纳入 routine 时,只允许更新 frontmatter 的运维字段。
4. **冲突检测** —— 先确认 INDEX 一致(见 `references/write-rules.md`);按标题/人名/项目名在 INDEX 找可能涉及的已有节点,读取候选,语义比对是否与新输入矛盾。**有冲突 → 高亮矛盾点,等用户裁决,不静默覆盖。**
5. **digest 扇出**(DESIGN.md §4.3):
   - 必产 1 个主记录节点(会议、群聊窗口 → `event`;外部读物、内部路线思考/方法论/调研/白皮书 → `reading`)。**会议簇**(同一场会的日历 + 投屏文档 + 妙记)仍只产 1 个 `event`,不按物件拆 —— 见 `references/digest-meeting.md`。
   - **资料型 `reading` 扇出规则**:若 `reading` 是外部读物,默认只产 `reading`,一般不抽 `decision`、不更新实体;若是内部路线思考 / 方法论 / 调研 / 白皮书,则 `reading` 是"这篇资料本身"的主记录,同时可按内容抽取明确决策、更新相关 `project`/`area`/`person`/`org`。不要把整篇资料硬塞进某个 `project` 或 `event`;项目节点只摘项目相关事实,决策节点只摘真正生效的决定。
   - 抽取 N 个 `decision`:输入中每个明确决策一节点。
   - 创建或更新涉及的实体节点(`person`/`project`/`org`/`area`)。
   - **实体消解**(DESIGN.md §4.3):建实体前在 INDEX 比对,命中则更新而非新建。`person` **必须先解析 `feishu_id`**(飞书英文 id、全局唯一;文档/群聊里的 open_id 用 `bin/resolve-users.sh` 解析,写进 person frontmatter `feishu_id`)。**新建 person 不允许写 `feishu_id: ?`**;解析失败时先不要建 person,在主记录正文保留姓名 / open_id 并汇报「待解析人物」。**同名陷阱** —— 中文名相同但 `feishu_id` 不同 = 不同的人,**不合并**、**向用户确认后**各自建节点;`project`/`org`/`area` 按名比对,有歧义问用户。
   - **参与方立场分析**(细则 `references/digest-analysis.md`):`event` 除字面结论外,对每个关键参与方分析其立场、利益/动机、对决策的态度,并沉淀进对应 `person` 节点。**必须基于发言证据**,区分【观察】与【推断】,证据不足标「证据有限」,**不做无证据的发散猜测**。
   - **思路与视角沉淀**(细则 `references/digest-analysis.md`):摄取时若有人(使用者/主管/同事)陈述了对某 `project`/`area` 的思路、想法、打法或意图 → 在该节点「思路与视角」章节追加一条带日期、带作者、带【主张】/【意图】标记的条目(按事件发生时间倒序)。第一方陈述用【主张】/【意图】,从发言推断仍用【推断】;**绝不把主观意图当成客观结论**。跨主题、不挂某个项目的工作底色不进节点,留给使用者维护 `context.md`。
   - **结合 `context.md` 重点关注**(操作前必读已把 `context.md` 当透镜加载):凡文档涉及 `context.md` 里记录的**使用者本人、其项目 / 团队、其关注的人(如直属领导)及这些人的指令 / 表态** —— 重点抽取、确保进入相应节点,不淡化、不漏。
   - **重点高亮**:文档若提到**重大事故、指标重大变化、或其它需要 highlight 的内容** → 在对应节点**显著记录**(如 `event` 的「结论」、`project` 的「关键进展 / 问题 / 风险」),并在汇报时**单独、突出**地提醒用户。
6. **写入** —— 每个节点按 `templates/node-<type>.md` 骨架生成,遵守 `references/write-rules.md`。
7. **汇报** —— 告诉用户:新建了哪些节点、更新了哪些、是否有冲突待裁决。若命中「重点高亮」内容(重大事故 / 指标剧变 / 涉及你或你关注的人的重要指令等)→ 单独、显眼地提醒。

## 规模预估

若输入很大(长文档、跨多业务/多表格、引用大量子文档),digest 前先预估本次会新建/更新约多少节点、牵出哪些子文档,告诉用户并确认摄取深度 —— **不无差别一次性铺开**。

## 分场景细则

| 场景 | 必读 |
|------|------|
| 摄取群聊(`feishu_chat`) | `references/digest-chat.md` |
| 摄取飞书文档(`feishu_doc`) | `references/digest-doc.md` |
| 摄取外部读物(`web`) / 内部资料型文档(`feishu_doc`) | `references/digest-reading.md` |
| 摄取一场会议(日历会议 / 投屏文档 + 妙记 同属一场会) | `references/digest-meeting.md` |
| 产出 `event` 立场分析 / 给 `project`·`area` 写「思路与视角」 | `references/digest-analysis.md` |
| 输入大(长文档 / 滚动周报 / 大群聊窗口,或规模预估提示要读大量正文) | 加读 `references/digest-large.md` —— 委派子 agent 在隔离上下文里摄取 |
| 不带来源的 `digest` / "跑定期摄取" / "检查周报更新" | `references/digest-routine.md` |

`feishu_minutes` / `feishu_meeting` 单独摄取无额外细则(但若它属于一场带投屏文档的会议 → 走上面「会议簇」行);`local_md` 直接读取本地文件 —— 按主流程执行即可。
