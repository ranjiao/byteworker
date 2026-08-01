# byteworker · digest 主流程

> 由 `SKILL.md`「digest」路由到这里。执行任何摄取前先读本文件,再按来源类型读取对应
> `references/digest-*.md` 细则。

## 触发

子命令 `digest`;或自然语言 —— 用户给出飞书文档/妙记 URL、会议、群、Meego / 多维表格视图 / 风神看板、
或本地 md 路径,说"存入知识库""消化这个""记一下""把 XX 群最近的讨论存进来"。

**不带来源**的 `digest`、或"跑定期摄取""检查周报更新" → 运行定期摄取(见 `references/digest-routine.md`)。

## 主流程

长流程状态输出:开始摄取时先告诉用户本次会经历「分类 → 拉原文 → 幂等检查 → 依赖判断 → 冲突检测 → 节点写入 → 回滚点」;下面每个阶段完成后回显一行短状态。若拉原文、依赖判断、人员解析、冲突检测或节点写入任一阶段耗时超过约 30-60 秒,按 `SKILL.md`「长流程状态输出」发 heartbeat,只说阶段和数量,不要贴原文。

1. **分类** —— 判定 `source_type`:`feishu_doc` / `feishu_minutes` / `feishu_meeting` /
   `feishu_chat` / `meego` / `feishu_base` / `aeolus` / `web` / `local_md`。**若输入是一整场会议**
   (日历会议链接 / 日程,或同属一场会的投屏文档 + 妙记多个 URL)→ 这是「会议簇」,整体摄取成
   一个 event,见下方场景细则。
   飞书 Wiki **单页**仍归 `feishu_doc`；空间首页、整库探索或子树选择属于前置
   `feishu_wiki` 探索，先按 `references/digest-wiki-space.md` 得到用户确认的页面列表，
   不把整棵树当成一篇来源。
2. **摄取原文**:
   - `feishu_doc` → 用 `lark-doc +fetch --api-version v2 --detail with-ids` 读取文档正文,并按
     `references/digest-comments.md` 独立读取全部评论(含已解决)、完整回复链和正文锚点。
     **摄取前必读** `references/digest-doc.md` 与它路由的评论细则。
   - `feishu_minutes` → 优先用 `lark-vc` / `lark-minutes` 取纪要、AI 产物(总结/待办/章节)、逐字稿;若只有会议号/日程,先用 `lark-vc` 定位会议产物和 minute token。抓取完成后通过 `source bundle --source-type feishu_minutes` 规范化逐字稿和 segment anchors。若能从妙记元数据、会议名/时间、日历日程、纪要正文中的文档引用找到对应会议文档,把这些链接记为 `related_source_urls` 并写入 event「事件信息」。
   - `feishu_meeting` → 用 `lark-vc` 取会议纪要产物;拿到 minute token 后再取妙记正文 / AI 产物。同步 best-effort 查找该会议的日历链接和会议文档链接,找到则写入 raw / event,找不到不臆造。
   - `feishu_chat` → 首次运行 `bin/byteworker run bin/pull-chat.sh` 确认 chat_id/窗口并保存 v2 Profile；后续运行 `source capture --source-type feishu_chat --kb ... --source-uid ... --out ...`，它包装完整分页、增量高水位、逐字稿/locator 并直接输出 Bundle。**摄取前必读** `references/digest-chat.md`。
   - `meego` → 先通过机器协议运行 `source inspect`,基于真实字段元数据选择并说明最小稳定投影，
     首次把权威坐标和投影保存为 `byteworker-source-profile/v2`；之后按 `--kb + --source-uid`
     capture。已有同源快照时由 SnapshotStore 直接从 KB 选择上一份完整快照并运行
     `source diff` 缩小语义复核范围。**摄取前必读** `references/digest-meego.md`。
   - `feishu_base` → 先通过机器协议运行 `source inspect`,用户确认字段后保存 v2 Profile 并运行 `source capture`
     串行拉完明确 Base 视图。**摄取前必读** `references/digest-base.md`。
   - `aeolus` → 先通过机器协议运行 `source inspect`，确认报表、dataset 和看板 public filters；
     再用 `source register --kb ...` 把这个 dashboard sheet 的独立 selector/filter profile
     写进用户 KB，最后只按 `source_uid` capture。**摄取前必读**
     `references/digest-aeolus.md`。
   - `web` → 外部读物(blog/论文/wiki):用宿主 agent 的网页抓取/浏览能力取得正文,本地 PDF / 文章则读取本地文件。**摄取前必读** `references/digest-reading.md`。
   - `local_md` → 直接读取本地文件，再通过 `source bundle --source-type local_md` 生成 Bundle。
   - `web` → 宿主浏览器抓取正文后，通过 `source bundle --source-type web` 生成 Bundle。
   失败按 `references/error-handling.md` 中止。
3. **幂等检查** —— 来源 adapter 把本次实际摄取的正文、评论、白板或结构化快照写成系统
   临时目录中的 `byteworker-source-bundle/v2`。bundle 是来源身份、外部 component、coverage、
   anchors 与 provider metadata 的唯一交接结构；通过机器协议运行
   `bin/digest-txn.py preflight`(完整格式与命令见
   `references/digest-transaction.md`)。脚本计算 `source_uid` / `source_revision` /
   `digest_period` 或 `source_window` / 逐组件 hash / `content_hash` / `digest_key`;Agent不得
   手算或覆盖这些值。飞书文档评论变化不依赖正文 revision,白板变化也属于 payload 变化,不能因
   正文未变而跳过。根据返回状态处理:
   - 完全相同 `digest_key` 已存在且 `digest_status: digested` → **no-op**:不写 raw、不改节点、
     不追加 journal;向用户说明"该来源同一版本已摄取过",并列出已有 `raw_id` / `digest_targets`。
   - 同一 `source_uid + digest_period/source_window` 但 `content_hash` 不同(含飞书文档
     `comment_hash` 单独变化)→ 视为同源新版本:
     继续流程,但后续必须更新已有主记录节点,不得另起重复 `reading` / `event` / `decision`。
   - 历史 raw 缺少 `digest_key` / `payload_schema` 时,脚本用
     `source_uid/source_url + digest_period/source_window + content_hash` 以及可比的
     `body_hash` / `comment_hash` / `whiteboard_hash` 做兼容判重。必要时只补 raw
     frontmatter 的运维字段,不得改 raw 正文。
4. **重要依赖判断(条件式用户闸门)** —— 默认范围仍只有用户当前指定的对象。扫描当前正文中的
   文档引用、历史会议、附件 / 嵌入表格、前置方案 / 决策 / 数据源等候选,按
   `references/digest-dependencies.md` 判断它是否是「缺失会实质影响本次 digest 正确性或完整性」
   的重要依赖。没有重要依赖 → 静默继续;有 → **在读取依赖正文和写节点前**,把候选、重要原因、
   建议范围合并成一次询问,由用户选择是否增加本次 digest 内容。未经同意不扩展;用户拒绝或暂缓时,
   当前对象照常 digest,但把受影响结论标为「依赖未摄取 / 待核实」。同场会议簇的组成物件仍按
   `references/digest-meeting.md` 的整体确认处理,不重复询问。
5. **准备 raw 计划** —— 决定唯一 `raw_id` 与 `raw_data/<YYYY-MM-DD>-<slug>.md`，在
   `digest-plan/v2` 里只引用已经校验的 source bundle，并写未摄取依赖与节点候选；不得把
   `source` 或 `provenance.anchors` 从 bundle 复制到 plan。此时不手工拼 raw。飞书文档、
   妙记 / 录屏、日历会议、网页等可打开来源必须由 bundle 保留
   用户可打开的 `source_url`。事务脚本会逐字拼入正文、canonical 评论/白板,自动写
   `ingested`、hash、`digest_key`、`digest_targets` 与 `digest_status: digested`。目标文件或
   `raw_id` 已存在时必须改用 `-2`/revision/hash 后缀,**绝不覆盖旧 raw**。
   同时按 `references/provenance.md` 把抓取阶段保留的稳定 locator 写成 bundle
   `anchors`;不要等摘要完成后按文本猜位置。
6. **冲突检测** —— 唯一动作表见 `references/conflict-policy.md`。先确认 INDEX 一致(见
   `references/write-rules.md`);按标题/人名/项目名、
   已有 raw 的 `digest_targets`、同源历史主记录节点在 INDEX 找可能涉及的已有节点,读取候选,
   语义比对是否与新输入矛盾。独立来源冲突时高亮矛盾点并等待用户裁决；只有可验证 revision、
   supersede 或用户明确确认才可按对应 disposition 更新，时间较新本身不构成覆盖依据。
7. **digest 扇出**(DESIGN.md §4.3):
   - 必产 1 个主记录节点(会议、群聊窗口 → `event`;外部读物、内部路线思考/方法论/调研/白皮书 → `reading`)。**会议簇**(同一场会的日历 + 投屏文档 + 妙记)仍只产 1 个 `event`,不按物件拆 —— 见 `references/digest-meeting.md`。
   - **同源主记录去重**:若同一 `source_uid + digest_period/source_window` 已有主记录节点(可从
     历史 raw `digest_targets`、节点 `sources` 或标题/链接召回),更新该节点,不要新建重复
     `reading` / `event`。`decision` 也按同一事实/同一来源去重;新版本改变原决策时,走
     supersede / 冲突裁决,不并排制造两个同义决策。
   - **结构化保存视图**:Meego / Base / 风神整个视图只产一张同源 `reading` 主记录。普通行/报表变化只留在
     raw + provenance；只有满足 `references/semantic-policy.md` 的晋升 reason code 和最低证据
     才进入实体图，不由 Agent 自行解释“长期/稳定/重要”。
     `left_view` 只表示离开视图，不得推断为删除。
   - **主记录来源链接**:`event` / `reading` 正文必须附上原始来源链接,不能只放 raw_id。`event`
     写在「事件信息」,包括原始文档 / 妙记 / 日历日程 / 已找到的会议文档;`reading` 写在「来源」。
     若是会议但没找到对应会议文档,可写“会议文档:未找到”或不写该项,不得编造链接。
   - **资料型 `reading` 扇出规则**:若 `reading` 是外部读物,默认只产 `reading`,一般不抽 `decision`、不更新实体;若是内部路线思考 / 方法论 / 调研 / 白皮书,则 `reading` 是"这篇资料本身"的主记录,同时可按内容抽取明确决策、更新相关 `project`/`area`/`person`/`org`。不要把整篇资料硬塞进某个 `project` 或 `event`;项目节点只摘项目相关事实,决策节点只摘真正生效的决定。
   - 抽取 N 个 `decision`:输入中每个明确决策一节点。
   - 创建或更新涉及的实体节点(`person`/`project`/`org`/`area`)。
   - **实体消解与通讯录补全**(DESIGN.md §4.3):建实体前在 INDEX 比对,命中则更新而非新建。
     `person` 必须把文档/群聊里的 open_id 交给
     `bin/byteworker run bin/resolve-users.sh --format json`，按 `feishu_id` 消解，并消费同一次查询返回的
     `enterprise_email`、`department_path` 与顶层 `resolved_at`。新建 person 必须写
     `feishu_id` 和 `directory_verified_at`;可见的邮箱/部门同步写入 frontmatter 与「基本信息」。
     更新已有 person 时也刷新通讯录字段：部门非空且变化则更新当前值，并在「协作历史与关键交互」
     保留带日期的旧部门 → 新部门；查询为空不清除旧值。`department_path` 只有明确命中已有
     `org` 才连边，不按路径片段自动建组织。**新建 person 不允许写 `feishu_id: ?`**;解析失败时
     先不要建 person,在主记录正文保留姓名 / open_id 并汇报「待解析人物」。**同名陷阱** ——
     中文名相同但 `feishu_id` 不同 = 不同的人,**不合并**、**向用户确认后**各自建节点;
     `project`/`org`/`area` 按名比对,有歧义问用户。
   - **参与方立场分析**(细则 `references/digest-analysis.md`):只分析
     `references/semantic-policy.md` 定义的关键参与方。立场必须绑定发言/行为 anchor；
     动机/利益默认不持久化，除非直接自述或至少两条独立可定位观察支持【推断】。
   - **思路与视角沉淀**(细则 `references/digest-analysis.md`):摄取时若有人(使用者/主管/同事)陈述了对某 `project`/`area` 的思路、想法、打法或意图 → 在该节点「思路与视角」章节追加一条带日期、带作者、带【主张】/【意图】标记的条目(按事件发生时间倒序)。第一方陈述用【主张】/【意图】,从发言推断仍用【推断】;**绝不把主观意图当成客观结论**。跨主题、不挂某个项目的工作底色不进节点,留给使用者维护 `context.md`。
   - **结合 `context.md` 重点关注**(操作前必读已把 `context.md` 当透镜加载):凡正文或评论涉及
     `context.md` 里记录的**使用者本人、其项目 / 团队、直属上司 / 汇报对象、用户点名特别关注的
     人员及这些人的指令 / 表态** —— 重点抽取、确保进入相应节点,不淡化、不漏。评论的 P0 / P1
     优先级与证据语义见 `references/digest-comments.md`;职位高只提高关注度,不自动提高事实置信度。
   - **重点高亮**:文档若提到**重大事故、指标重大变化、或其它需要 highlight 的内容** → 在对应节点**显著记录**(如 `event` 的「结论」、`project` 的「关键进展 / 问题 / 风险」),并在汇报时**单独、突出**地提醒用户。
   - **Todo 候选识别**(细则 `references/todo.md`):结合 `context.md` 的“我的身份 / 我的职责范围 / 当前重点”,识别明确 @本人 / 指派给本人,或职责范围内需要用户关注的行动、DDL、风险、待确认项。明确分配给别人、已完成 / 取消、一般广播不列候选;模型推导只能标“推断”。来源待办照常写进 event / report,但**未经用户确认不得写 `todo.md`**。
8. **写入事务** —— Agent按 `templates/node-<type>.md` 生成每个节点的**完整候选文件**;
   所有节点显式给 `evidence`,新节点至少一条；主记录设置 `primary_source`,关键事实句尾写
   `[E<n>]`,plan 中逐条映射到 `raw_id + anchor_id`;
   更新节点时记录读取基线的 `base_sha256`,并把本次新增/删除 link 的反向节点一并纳入 plan。依次运行
   通过机器协议运行 `bin/digest-txn.py validate` 与 `execute`:它会校验候选,原子写 raw/节点,重建 INDEX,追加
   journal,精确暂存本次路径并在知识库本地 git 创建 commit。只有 receipt
   `status=committed` 才算完成;`status=noop` 不得重复写。详见
   `references/digest-transaction.md` 与 `references/write-rules.md`。多份来源需要共同更新节点
   或必须同成同败时，使用只引用各 SourceBundle 的 `digest-batch-plan/v2`，不得拆成多个可能
   留下半成品的提交；v1 只兼容历史调用。
9. **汇报** —— 以事务 receipt 为准告诉用户 commit、raw_id、新建/更新节点、warning、是否因幂等检查跳过或合并了重复来源;不得仅凭 Agent已生成候选就声称落库。若发现重要依赖,还要说明哪些已随本次摄取、哪些未摄取及其影响。若命中「重点高亮」内容(重大事故 / 指标剧变 / 涉及你或你关注的人的重要指令等)→ 单独、显眼地提醒。若有 Todo 候选,末尾一次性列最多 5 项“事项 / 与你相关的依据 / 时间 / 来源”,询问哪些加入;用户回复序号 / 全部后再写 `todo.md`。最终汇报前不要让用户等到最后才第一次看到进展。

## 规模预估

若输入很大(长文档、跨多业务/多表格、引用大量子文档),digest 前先预估本次会新建/更新约多少节点、牵出哪些子文档,告诉用户并确认摄取深度 —— **不无差别一次性铺开**。

## 分场景细则

| 场景 | 必读 |
|------|------|
| 所有标准 digest | `references/provenance.md` |
| 摄取群聊(`feishu_chat`) | `references/digest-chat.md` |
| 摄取 Meego 保存视图(`meego`) | `references/digest-meego.md` |
| 摄取多维表格视图(`feishu_base`) | `references/digest-base.md` |
| 摄取风神看板(`aeolus`) | `references/digest-aeolus.md` |
| 摄取飞书文档(`feishu_doc`) | `references/digest-doc.md` |
| 探索飞书知识库空间、选择待 digest 页面 | `references/digest-wiki-space.md` |
| 飞书文档含内嵌白板 | 加读 `references/digest-whiteboard.md` |
| 摄取外部读物(`web`) / 内部资料型文档(`feishu_doc`) | `references/digest-reading.md` |
| 摄取一场会议(日历会议 / 投屏文档 + 妙记 同属一场会) | `references/digest-meeting.md` |
| 产出 `event` 立场分析 / 给 `project`·`area` 写「思路与视角」 | `references/digest-analysis.md` |
| 输入大(长文档 / 滚动周报 / 大群聊窗口,或规模预估提示要读大量正文) | 加读 `references/digest-large.md` —— 委派子 agent 在隔离上下文里摄取 |
| 不带来源的 `digest` / "跑定期摄取" / "检查周报更新" | `references/digest-routine.md` |

`feishu_minutes` / `feishu_meeting` 单独摄取无额外细则(但若它属于一场带投屏文档的会议 → 走上面「会议簇」行);`local_md` 直接读取本地文件 —— 按主流程执行即可。
