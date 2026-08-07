# byteworker · digest 细则 —— 读物 / 资料卡(reading)

> 由 `SKILL.md`「digest」一节路由到这里。摄取外部读物(`source_type: web`),或内部资料型文档(`source_type: feishu_doc`,如路线思考 / 方法论 / 调研 / 技术白皮书)前必读本文件。

`reading` 是"这篇资料本身"的 digest,不是项目状态、会议快照或决策本体。它用于保存可复用的观点、方法、框架、证据和启发。

- **抓取**:web URL → 用宿主 agent 的网页抓取/浏览能力取正文;本地 PDF / 文章 → 读取本地文件;飞书内部资料 → 先按 `references/digest-doc.md` 用 `lark-doc +fetch --api-version v2` 取正文。
- **统一交接**:Web 正文抓取后走 `source bundle --source-type web`；本地 Markdown 走
  `source bundle --source-type local_md`；飞书内部资料仍走 `feishu_doc` adapter。浏览器和
  本地文件读取不塞进 `source.py` transport，但进入 digest 前必须得到同一 SourceBundle。
- **外部读物**:blog / 论文 / wiki 通常弱相关于工作,一篇文章 → **1 个 `reading` 节点**(写入 `knowledge/readings/`),提炼核心观点 + 可借鉴点。默认不产 `event` / `decision`,一般不动工作实体节点。
- **内部资料型文档**:路线思考、方法论、调研、技术白皮书、方案复盘、原则阐释等,一篇资料 → **1 个 `reading` 主记录节点**。同时可按内容扇出:
  - 明确已经生效的选择 / 原则 / 边界 → `decision`;
  - 有生命周期的专项 / 产品 / 技术建设 → 更新或创建 `project`;
  - 常青方法 / 规范 / how-to / 踩坑 → 更新或创建 `area`;
  - 有证据的人物主张 / 意图 → 更新 `person` 的「立场 / 利益 / 动机」或相关项目「思路与视角」。
- **内部资料标题消歧**:内部资料的原始标题经常只在作者或团队上下文里成立。写入 `reading`
  时不要机械使用原题；若标题缺少范围，必须用可证实的作者、团队、业务、项目或周期补成库内
  唯一可读标题。例:`个人工作总结` → `<作者>：<周期>个人工作总结`；`直播模型算法方案` →
  `<团队或项目>：直播模型算法方案`。标题消歧只作用于 `knowledge` 节点的 title/H1/TL;DR，
  原始标题继续保存在 raw 的 `source_title` 与「来源」里。无法确认作者、团队或项目时，在
  「来源」或 TL;DR 标注“归属待确认”，不要把个人材料上升为团队方案。
- **边界**:不要因为资料里提到某项目,就把整篇资料塞进项目节点;项目节点只摘与项目状态、策略、进展、风险相关的事实。不要因为资料里有"建议 / 思考 / 可行性"就建 `decision`;只有明确拍板、生效或被当作行动原则执行的内容才建 `decision`。
- **链接**:外部读物若明显关联某 `area` / `project`,可连 `links`;否则留空。内部资料型文档通常要连到其支撑或影响的 `project` / `area` / `decision` / `event`,作为资料入口。
- `reading` 低维护:观点不过期,`status` 恒为 `current`,不进新鲜度 / 看板 ⚠️ 逻辑。
