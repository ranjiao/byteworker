# byteworker · digest 细则 —— 文档摄取(feishu_doc)

> 由 `SKILL.md`「digest」一节路由到这里。摄取飞书文档(`source_type: feishu_doc`)前必读本文件。

飞书文档(尤其调研 / 规划类)常是「枢纽文档」,摄取规则:

- **滚动周会 / 周报文档(默认只取最近周期)**:有的文档是「一篇持续追加」的滚动周会 / 周报 —— 每个周期是一个顶层标题块(通常为日期,如 `# 20260520`,**新周期排在最前**),整篇累积数周乃至数月、可能很大。digest 这类文档**默认只摄取最近一个周期**(最靠前的日期块),跳过「模版 / template」之类占位块。`raw_data` 只落该周期内容(非整篇),frontmatter 标注周期标识(`digest_period`)。`digest_period` 若是日期,必须按 DESIGN.md §2.1 规范化为 `YYYY-MM-DD`(如 `20260520` → `2026-05-20`,`5-21` 在当前年份语境下 → `2026-05-21`);raw 正文标题仍逐字保留。摄取后告诉用户「取了哪个规范化周期、文档里还有哪些更早周期」;用户要更早某期或全部,再单独 digest。识别特征:顶层标题是一串连续日期、各周期结构雷同。首次摄取此类文档后,**询问用户是否纳入「定期摄取」**(见 `references/digest-routine.md`)。
- **内部资料型文档 → `reading` 主记录**:若文档不是会议纪要/周报/项目状态,而是路线思考、方法论、调研、技术白皮书、方案复盘、原则阐释等"认知资产",主产 1 个 `reading` 节点(资料卡),并加读 `references/digest-reading.md`。`reading` 记录这篇资料本身的核心观点、方法框架、适用边界和可借鉴点;同时可按内容扇出明确 `decision`、更新相关 `project`/`area`/`person`/`org`。不要把整篇资料硬塞进某个项目或事件节点,项目只摘项目相关事实,事件只用于真实会议/评审/发布/讨论窗口。
- **人员 @ 提及解析**:`lark-doc` 返回的 `<cite type="user">` 是裸 `open_id`。digest 前运行 `bin/resolve-users.sh --from-doc <原文文件>`(或 `--ids ou_x,ou_y`)拿到 `open_id → 姓名 → feishu_id`(飞书英文 id = 企业邮箱前缀)映射,再建 / 更新 `person` 节点 —— `feishu_id` 写进 person frontmatter 字段(person 的 id 是稳定 slug、不随 `feishu_id` 变,见 DESIGN.md §2)。**不要手写解析逻辑。新建 person 必须有解析出的 `feishu_id`;解析不到则先不建 person,在 event / project 正文保留姓名或 open_id 并向用户报告待解析。**
- **同名消歧**:person 实体消解**按 `feishu_id` 比对**(全局唯一);**中文名相同但 `feishu_id` 不同 = 不同的人 → 不合并、向用户确认后各自建节点**;解析失败时先不建 person,向用户报告待解析人物(详见 `SKILL.md` digest 第 5 步「实体消解」)。
- **嵌入电子表格 / 多维表格**:文档里的 `<sheet>` / bitable 只返回占位 token,**关键数据在表格内**。需要这些数据时用 `lark-sheets` / `lark-base` 下钻取数;不下钻则在「关联文档与会议」登记该表并标注"数据在表格内"。
- **引用的子文档**:文档里 `<cite type=doc>` 引用的其他文档 → **登记进项目节点的「关联文档与会议」**;**不自动递归摄取**(会爆炸),而是把这些子文档列给用户,由用户决定是否进一步摄取。

> `feishu_minutes`(妙记)/ `feishu_meeting`(会议)用 `lark-vc` / `lark-minutes` 取产物:会议号 / 日程优先 `lark-vc` 定位会议与 minute token,妙记 URL 可直接取妙记产物。扇出与写入按 `SKILL.md`「digest」主干。
