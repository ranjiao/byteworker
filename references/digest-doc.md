# byteworker · digest 细则 —— 文档摄取(feishu_doc)

> 由 `SKILL.md`「digest」一节路由到这里。摄取飞书文档(`source_type: feishu_doc`)前必读本文件。

飞书文档(尤其调研 / 规划类)常是「枢纽文档」,摄取规则:

- **正文与评论必须双路读取**:先读 `references/digest-comments.md`。正文用
  `lark-doc +fetch --api-version v2 --detail with-ids`;评论用
  `bin/byteworker run bin/pull_doc_comments.py --url "<URL>"`,固定拉取全部评论(含已解决)、完整回复链与
  relation 锚点。正文 fetch 成功不代表评论已读取;评论状态必须在 raw frontmatter 明示。
  同时按 `references/provenance.md` 把 `with-ids` 的正文 block id、评论 / 回复 id 转成
  provenance anchors;关键事实节点正文用 `[E<n>]` 绑定到相应 anchor。
- **正文内嵌白板属于当前来源 payload**:正文出现 `<whiteboard token="...">` 时加读
  `references/digest-whiteboard.md`,默认读取每个白板的结构化节点 JSON 与整体预览;结构 JSON
  以独立 `kind=whiteboard` component 进入 source bundle。不能只保留占位 token 或只看截图。
  当前文档自身白板不算递归子文档;外部白板和其它文档里的白板仍走重要依赖闸门。
- **统一交接**:正文、评论和白板 artifact 抓取完成后，调用
  `source bundle --source-type feishu_doc --request <request.json> --out <bundle.json>`。
  `--request` 是临时 JSON 文件路径，不是内联 JSON；不清楚字段时先运行
  `source bundle-spec --source-type feishu_doc`。若 lark-cli fetch 保存的是完整 JSON wrapper，
  body 可直接用
  `{"path":"<fetch.json>","mode":"verbatim","json_pointer":"/data/document/content"}`，
  不必另写脚本提取纯文本。
  评论策略关闭或权限不可用时省略 comments component，并明确
  `provider_metadata.comments_status=unavailable`；不得提供空文件伪装完整评论。
- **文档来源标识与幂等键**:`lark-doc +fetch --api-version v2` 返回的 `document_id` 和
  `revision_id` 必须写入 raw frontmatter:`source_uid=<document_id>`、`source_revision=<revision_id>`。
  `source_uid` 不添加 `feishu_doc:` 前缀；adapter 会拒绝这种重复命名空间。
  同一文档 URL 可能带不同 query 参数,不得用完整 URL 作为唯一判重依据;URL 只保留在
  `source_url` 便于回溯。`source_url` 必须是用户可打开的原始文档链接;后续生成的 `event` /
  `reading` 主记录正文也必须在「事件信息」或「来源」写出该链接,不能只引用 `raw_id`。
  飞书文档 adapter 把正文、评论、白板 component、coverage 和 anchors 统一写入
  `byteworker-source-bundle/v2`，再通过机器协议交给 `bin/digest-txn.py preflight`:脚本计算
  `body_hash`、`comment_hash`、每个白板 hash 与组合 `content_hash` 并组成 `digest_key`
  (见 DESIGN.md §3 / `references/digest-transaction.md`)。完全相同 key 已存在时
  直接 no-op;同一 `document_id` 但 hash 变化时,按同源新版本更新已有主记录节点。
- **滚动周会 / 周报文档(默认只取最近周期)**:有的文档是「一篇持续追加」的滚动周会 / 周报 —— 每个周期是一个顶层标题块(通常为日期,如 `# 20260520`,**新周期排在最前**),整篇累积数周乃至数月、可能很大。digest 这类文档**默认只摄取最近一个周期**(最靠前的日期块),跳过「模版 / template」之类占位块。`raw_data` 只落该周期内容(非整篇),frontmatter 标注周期标识(`digest_period`)。`digest_period` 若是日期,必须按 DESIGN.md §2.1 规范化为 `YYYY-MM-DD`(如 `20260520` → `2026-05-20`,`5-21` 在当前年份语境下 → `2026-05-21`);raw 正文标题仍逐字保留。摄取后告诉用户「取了哪个规范化周期、文档里还有哪些更早周期」;用户要更早某期或全部,再单独 digest。识别特征:顶层标题是一串连续日期、各周期结构雷同。首次摄取此类文档后,**询问用户是否纳入「定期摄取」**(见 `references/digest-routine.md`)。
  - 滚动文档的 `digest_key` 必须按
    `document_id + digest_period + 本周期实际 payload content_hash` 判重,不是整篇文档的最新
  revision。若最新周期正文不变但相关评论 / 回复或本周期白板变化,仍是增量;若只有其它旧周期
    正文被编辑且本周期实际 component 都不变,默认 no-op。新版本更新同一个事件 / 周报主记录。
  - 如果用户明确要求 digest 更早某期,该期的 `digest_period` 使用对应规范化日期 / ISO 周,
    可与同文档其它周期并存,但每个周期仍按 `document_id + digest_period + content_hash` 幂等。
  - 用户确认纳入 routine 时，把 document identity、周期策略、评论/白板策略与 cadence 保存为
    `byteworker-source-profile/v2`；后续复查按 profile 重放，不再从最近 raw 猜抓取参数。
- **内部资料型文档 → `reading` 主记录**:若文档不是会议纪要/周报/项目状态,而是路线思考、方法论、调研、技术白皮书、方案复盘、原则阐释等"认知资产",主产 1 个 `reading` 节点(资料卡),并加读 `references/digest-reading.md`。`reading` 记录这篇资料本身的核心观点、方法框架、适用边界和可借鉴点;同时可按内容扇出明确 `decision`、更新相关 `project`/`area`/`person`/`org`。不要把整篇资料硬塞进某个项目或事件节点,项目只摘项目相关事实,事件只用于真实会议/评审/发布/讨论窗口。
  - 同一 `document_id` 的内部资料型文档重复 digest 时,默认更新已有 `reading` 主记录;只有用户
    明确要求把不同版本作为独立资料归档,才新建带版本后缀的 `reading`。
- **人员 @ 提及解析**:`lark-doc` 返回的 `<cite type="user">` 是裸 `open_id`。digest 前运行
  `bin/byteworker run bin/resolve-users.sh --from-doc <原文文件> --format json`(或 `--ids ou_x,ou_y`)取得
  `open_id / 姓名 / feishu_id / enterprise_email / department_path` 与顶层 `resolved_at`。
  建 / 更新 `person` 时按 `references/digest-core.md` 同步身份和当前通讯录画像：
  `resolved_at` 写为 `directory_verified_at`，可见的企业邮箱/部门写进 frontmatter 与「基本信息」。
  **不要手写解析逻辑。新建 person 必须有解析出的 `feishu_id`;解析不到则先不建 person,在
  event / project 正文保留姓名或 open_id 并向用户报告待解析。**
- **同名消歧**:person 实体消解**按 `feishu_id` 比对**(全局唯一);**中文名相同但 `feishu_id` 不同 = 不同的人 → 不合并、向用户确认后各自建节点**;解析失败时先不建 person,向用户报告待解析人物(详见 `references/digest-core.md`「digest 扇出」的实体消解规则)。
- **嵌入电子表格 / 多维表格**:文档里的 `<sheet>` / bitable 只返回占位 token,**关键数据在表格内**。
  先按 `references/digest-dependencies.md` 判断表内数据是否是当前结论 / 决策 / 风险成立所必需的
  重要依赖;是 → 向用户询问是否把该表加入本次 digest,同意后才用 `lark-sheets` / `lark-base`
  下钻取数;否或用户暂缓 → 在「关联文档与会议」登记该表并标注「数据在表格内,本次未摄取」。
- **引用的子文档 / 历史会议**:`<cite type=doc>`、正文链接或具名引用默认只登记进项目节点的
  「关联文档与会议」(标题 + 日期 / 周期 + 原始链接),**不自动递归摄取**。只有按
  `references/digest-dependencies.md` 判定为重要依赖时才列给用户并询问是否扩展本次 digest;
  普通背景引用 / 延伸阅读无需逐条打断用户。

> `feishu_minutes`(妙记)/ `feishu_meeting`(会议)用 `lark-vc` / `lark-minutes` 取产物:会议号 / 日程优先 `lark-vc` 定位会议与 minute token,妙记 URL 可直接取妙记产物。扇出与写入按 `SKILL.md`「digest」主干。
