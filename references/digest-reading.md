# byteworker · digest 细则 —— 外部读物(web)

> 由 `SKILL.md`「digest」一节路由到这里。摄取外部读物(`source_type: web`)前必读本文件。

外部读物(blog / 论文 / wiki)弱相关于工作,摄取规则:

- **抓取**:web URL → 用宿主 agent 的网页抓取/浏览能力取正文;本地 PDF / 文章 → 读取本地文件。
- **digest**:一篇文章 → **1 个 `reading` 节点**(写入 `knowledge/readings/`),提炼 核心观点 + 可借鉴点(对工作的潜在启发)。**不产 event / decision,一般不动工作实体节点。**
- **链接**:摄取时若文章明显关联某 `area` / `project`,可连 `links`;否则留空 —— 关联靠日后工作引用时再长出来。
- `reading` 低维护:观点不过期,`status` 恒为 `current`,不进新鲜度 / 看板 ⚠️ 逻辑。
