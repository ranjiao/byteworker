# byteworker · digest 细则 —— 文档摄取(feishu_doc)

> 由 `SKILL.md`「digest」一节路由到这里。摄取飞书文档(`source_type: feishu_doc`)前必读本文件。

飞书文档(尤其调研 / 规划类)常是「枢纽文档」,摄取规则:

- **滚动周会 / 周报文档(默认只取最近周期)**:有的文档是「一篇持续追加」的滚动周会 / 周报 —— 每个周期是一个顶层标题块(通常为日期,如 `# 20260520`,**新周期排在最前**),整篇累积数周乃至数月、可能很大。digest 这类文档**默认只摄取最近一个周期**(最靠前的日期块),跳过「模版 / template」之类占位块。`raw_data` 只落该周期内容(非整篇),frontmatter 标注周期标识(`digest_period`)。摄取后告诉用户「取了哪个周期、文档里还有哪些更早周期」;用户要更早某期或全部,再单独 digest。识别特征:顶层标题是一串连续日期、各周期结构雷同。首次摄取此类文档后,**询问用户是否纳入「定期摄取」**(见 `references/digest-routine.md`)。
- **人员 @ 提及解析**:`lark-doc` 返回的 `<cite type="user">` 是裸 `open_id`(不像群聊会自动解析姓名)。digest 前运行 `bin/resolve-users.sh --from-doc <原文文件>`(或 `--ids ou_x,ou_y`)拿到 `open_id → 姓名 → feishu_id`(飞书英文 id = 企业邮箱前缀)映射,再建 / 更新 `person` 节点 —— `feishu_id` 写进 person frontmatter、并优先用作其 slug。**不要手写解析逻辑。**
- **同名消歧**:person 实体消解**按 `feishu_id` 比对**(全局唯一);**中文名相同但 `feishu_id` 不同 = 不同的人 → 不合并、向用户确认后各自建节点**;`feishu_id` 拿不到而 KB 有同名 person → 提示用户确认是否同一人(详见 `SKILL.md` digest 第 5 步「实体消解」)。
- **嵌入电子表格 / 多维表格**:文档里的 `<sheet>` / bitable 只返回占位 token,**关键数据在表格内**。需要这些数据时用 `lark-sheets` / `lark-base` 下钻取数;不下钻则在「关联文档与会议」登记该表并标注"数据在表格内"。
- **引用的子文档**:文档里 `<cite type=doc>` 引用的其他文档 → **登记进项目节点的「关联文档与会议」**;**不自动递归摄取**(会爆炸),而是把这些子文档列给用户,由用户决定是否进一步摄取。

> `feishu_minutes`(妙记)/ `feishu_meeting`(会议)用 `lark-minutes` / `lark-vc` 取产物,无额外细则 —— 扇出与写入按 `SKILL.md`「digest」主干。
