# byteworker · digest 主流程

> 由 `SKILL.md`「digest」路由到这里。执行任何摄取前先读本文件,再按来源类型读取对应
> `references/digest-*.md` 细则。

## 触发

子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、或本地 md 路径,说"存入知识库""消化这个""记一下""把 XX 群最近的讨论存进来"。

**不带来源**的 `digest`、或"跑定期摄取""检查周报更新" → 运行定期摄取(见 `references/digest-routine.md`)。

## 主流程

长流程状态输出:开始摄取时先告诉用户本次会经历「分类 → 拉原文 → 幂等检查 → 冲突检测 → 节点写入 → 回滚点」;下面每个阶段完成后回显一行短状态。若拉原文、人员解析、冲突检测或节点写入任一阶段耗时超过约 30-60 秒,按 `SKILL.md`「长流程状态输出」发 heartbeat,只说阶段和数量,不要贴原文。

1. **分类** —— 判定 `source_type`:`feishu_doc` / `feishu_minutes` / `feishu_meeting` / `feishu_chat` / `web` / `local_md`。**若输入是一整场会议**(日历会议链接 / 日程,或同属一场会的投屏文档 + 妙记多个 URL)→ 这是「会议簇」,整体摄取成一个 event,见下方场景细则。
2. **摄取原文**:
   - `feishu_doc` → 用 `lark-doc +fetch --api-version v2` 读取文档正文。**摄取前必读** `references/digest-doc.md`。
   - `feishu_minutes` → 优先用 `lark-vc` / `lark-minutes` 取纪要、AI 产物(总结/待办/章节)、逐字稿;若只有会议号/日程,先用 `lark-vc` 定位会议产物和 minute token。若能从妙记元数据、会议名/时间、日历日程、纪要正文中的文档引用找到对应会议文档,把这些链接记为 `related_source_urls` 并写入 event「事件信息」。
   - `feishu_meeting` → 用 `lark-vc` 取会议纪要产物;拿到 minute token 后再取妙记正文 / AI 产物。同步 best-effort 查找该会议的日历链接和会议文档链接,找到则写入 raw / event,找不到不臆造。
   - `feishu_chat` → 运行 `bin/pull-chat.sh` 拉取群聊(底层调 lark-im,自动定位群 + 分页拉全 + 输出逐字转写)。**摄取前必读** `references/digest-chat.md`。
   - `web` → 外部读物(blog/论文/wiki):用宿主 agent 的网页抓取/浏览能力取得正文,本地 PDF / 文章则读取本地文件。**摄取前必读** `references/digest-reading.md`。
   - `local_md` → 直接读取本地文件。
   失败按 `references/error-handling.md` 中止。
3. **幂等检查** —— 在写 raw 前,先为本次实际摄取正文计算 `source_uid` / `source_revision`
   / `digest_period` 或 `source_window` / `content_hash` / `digest_key`(字段含义见 DESIGN.md §3),
   并扫描 `raw_data/`:
   - 完全相同 `digest_key` 已存在且 `digest_status: digested` → **no-op**:不写 raw、不改节点、
     不追加 journal;向用户说明"该来源同一版本已摄取过",并列出已有 `raw_id` / `digest_targets`。
   - 同一 `source_uid + digest_period/source_window` 但 `content_hash` 不同 → 视为同源新版本:
     继续流程,但后续必须更新已有主记录节点,不得另起重复 `reading` / `event` / `decision`。
   - 历史 raw 缺少 `digest_key` 时,用 `source_uid/source_url + digest_period/source_window +
     content_hash` 近似比对;若正文 hash 相同,也按已摄取处理。必要时只补 raw frontmatter 的
     运维字段,不得改 raw 正文。
4. **落原文** —— 写 `raw_data/<YYYY-MM-DD>-<slug>.md`:逐字原文 + frontmatter(`digest_status:
   pending`)。frontmatter 必须尽量写 `source_url`(用户可打开的原始链接);会议 / 资料簇若发现
   其它同源物件,写 `related_source_urls`。**raw 正文一旦写入永不改写**;digest 完成 / 失败 / 纳入 routine 时,只允许更新
   frontmatter 的运维字段。若目标文件或 `raw_id` 已存在,必须追加 `-2`/`-3` 或 revision/hash
   后缀生成唯一文件名,**绝不覆盖旧 raw**。
5. **冲突检测** —— 先确认 INDEX 一致(见 `references/write-rules.md`);按标题/人名/项目名、
   已有 raw 的 `digest_targets`、同源历史主记录节点在 INDEX 找可能涉及的已有节点,读取候选,
   语义比对是否与新输入矛盾。**有冲突 → 高亮矛盾点,等用户裁决,不静默覆盖。**
6. **digest 扇出**(DESIGN.md §4.3):
   - 必产 1 个主记录节点(会议、群聊窗口 → `event`;外部读物、内部路线思考/方法论/调研/白皮书 → `reading`)。**会议簇**(同一场会的日历 + 投屏文档 + 妙记)仍只产 1 个 `event`,不按物件拆 —— 见 `references/digest-meeting.md`。
   - **同源主记录去重**:若同一 `source_uid + digest_period/source_window` 已有主记录节点(可从
     历史 raw `digest_targets`、节点 `sources` 或标题/链接召回),更新该节点,不要新建重复
     `reading` / `event`。`decision` 也按同一事实/同一来源去重;新版本改变原决策时,走
     supersede / 冲突裁决,不并排制造两个同义决策。
   - **主记录来源链接**:`event` / `reading` 正文必须附上原始来源链接,不能只放 raw_id。`event`
     写在「事件信息」,包括原始文档 / 妙记 / 日历日程 / 已找到的会议文档;`reading` 写在「来源」。
     若是会议但没找到对应会议文档,可写“会议文档:未找到”或不写该项,不得编造链接。
   - **资料型 `reading` 扇出规则**:若 `reading` 是外部读物,默认只产 `reading`,一般不抽 `decision`、不更新实体;若是内部路线思考 / 方法论 / 调研 / 白皮书,则 `reading` 是"这篇资料本身"的主记录,同时可按内容抽取明确决策、更新相关 `project`/`area`/`person`/`org`。不要把整篇资料硬塞进某个 `project` 或 `event`;项目节点只摘项目相关事实,决策节点只摘真正生效的决定。
   - 抽取 N 个 `decision`:输入中每个明确决策一节点。
   - 创建或更新涉及的实体节点(`person`/`project`/`org`/`area`)。
   - **实体消解**(DESIGN.md §4.3):建实体前在 INDEX 比对,命中则更新而非新建。`person` **必须先解析 `feishu_id`**(飞书英文 id、全局唯一;文档/群聊里的 open_id 用 `bin/resolve-users.sh` 解析,写进 person frontmatter `feishu_id`)。**新建 person 不允许写 `feishu_id: ?`**;解析失败时先不要建 person,在主记录正文保留姓名 / open_id 并汇报「待解析人物」。**同名陷阱** —— 中文名相同但 `feishu_id` 不同 = 不同的人,**不合并**、**向用户确认后**各自建节点;`project`/`org`/`area` 按名比对,有歧义问用户。
   - **参与方立场分析**(细则 `references/digest-analysis.md`):`event` 除字面结论外,对每个关键参与方分析其立场、利益/动机、对决策的态度,并沉淀进对应 `person` 节点。**必须基于发言证据**,区分【观察】与【推断】,证据不足标「证据有限」,**不做无证据的发散猜测**。
   - **思路与视角沉淀**(细则 `references/digest-analysis.md`):摄取时若有人(使用者/主管/同事)陈述了对某 `project`/`area` 的思路、想法、打法或意图 → 在该节点「思路与视角」章节追加一条带日期、带作者、带【主张】/【意图】标记的条目(按事件发生时间倒序)。第一方陈述用【主张】/【意图】,从发言推断仍用【推断】;**绝不把主观意图当成客观结论**。跨主题、不挂某个项目的工作底色不进节点,留给使用者维护 `context.md`。
   - **结合 `context.md` 重点关注**(操作前必读已把 `context.md` 当透镜加载):凡文档涉及 `context.md` 里记录的**使用者本人、其项目 / 团队、其关注的人(如直属领导)及这些人的指令 / 表态** —— 重点抽取、确保进入相应节点,不淡化、不漏。
   - **重点高亮**:文档若提到**重大事故、指标重大变化、或其它需要 highlight 的内容** → 在对应节点**显著记录**(如 `event` 的「结论」、`project` 的「关键进展 / 问题 / 风险」),并在汇报时**单独、突出**地提醒用户。
7. **写入** —— 每个节点按 `templates/node-<type>.md` 骨架生成,遵守 `references/write-rules.md`。
8. **汇报** —— 告诉用户:新建了哪些节点、更新了哪些、是否有冲突待裁决、是否因幂等检查跳过或合并了重复来源。若命中「重点高亮」内容(重大事故 / 指标剧变 / 涉及你或你关注的人的重要指令等)→ 单独、显眼地提醒。最终汇报前不要让用户等到最后才第一次看到进展。

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
