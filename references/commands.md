# byteworker · 子命令细则

> 由 `SKILL.md` 路由到这里。执行 `search` / `update` / `brief` / `dashboard` / `context`
> 前按需读取对应小节。

## search — 查询

**触发**:子命令 `search`;或自然语言 —— "关于X我知道什么""我们关于Y定过什么""Z项目现在怎样"。

1. **确定性候选召回**:先按机器协议运行
   `python3 bin/byteworker-cli.py kb-query search --kb "<知识库目录>" --query "<查询>" --limit 12 --graph-depth 1 --max-nodes 30`。
   脚本统一扫描 id / 标题 / tag / TL;DR / body，并在预算内扩展一跳 links，输出覆盖数、分数和
   命中理由；它不保存索引、不做语义裁决。若用户用的是抽象表达或候选不足，Agent 再用近义词
   重跑，并语义检查 `INDEX.md`，把补充候选与脚本候选取并集。
2. **结构化大 raw 召回(按场景必做)** —— 用户询问 Meego / Base / 风神中的具体需求、记录/报表 ID、
   标题或其状态字段时，即使知识节点已有同一看板的宏观摘要，也必须继续调用：
   `python3 bin/byteworker-cli.py kb-query source-record --kb "<知识库目录>" --source-type meego --record-id "<稳定 ID>"`
   或
   `python3 bin/byteworker-cli.py kb-query source-record --kb "<知识库目录>" --source-type meego --title "<需求标题或用户表述>"`。
   Base 使用 `--source-type feishu_base`；风神使用 `--source-type aeolus`，稳定 ID 为
   `report:<report_id>`。标题由 Python 做 Unicode / 大小写 / 空白 / 标点归一化、
   包含、分词覆盖与字符相似度排序；返回多条时结合 `score / match.kind / source_uid` 判断，
   分数接近或跨来源同名必须向用户披露歧义，不得擅选。默认每个 `source_uid` 只查最新完整快照；
   当前未命中但用户明确要历史记录时才加 `--history`，并把 `is_latest_snapshot=false` 说明为
   历史证据。**禁止用 `rg` / `grep` 或让 Agent 直接读取完整结构化 raw 作为主检索方式**；
   `source-record` 的 coverage / parse_warnings 不完整时必须披露。
3. **图遍历(检索承重墙,必做)** —— 定向读取候选节点，并综合脚本给出的一跳邻居。关系型问题
   仍可在 `max-nodes` 预算内继续人工定向读取，不得无界展开全库。
4. **溯源解析(必做)** —— 对准备写入答案的每条事实,从实际承载该事实的节点 `sources`
   反查 `raw_data/` frontmatter,建立“结论 → 原始来源”映射。完整执行
   `references/citations.md`:不能止步于节点 id / raw_id / 报告路径;必须解析出具体原始文档 /
   妙记录屏 / 会议 / 群聊窗口、`ingested` 收录时间及能确认的原文时间 / 覆盖范围。报告和
   journal 是二手入口,继续追到 raw;缺失项照实披露。
   节点已有 `[E<n>]` 时，优先运行
   `python3 bin/byteworker-cli.py kb-query evidence --kb "<知识库目录>" --node "<node-id>" --markers E1,E2`
   解析 raw、sidecar 与精确 anchor；历史节点没有证据表时再退回 `sources` 级溯源。
5. **置信度(必报)**:
   - **高**:命中 ≥1 条直接相关节点,且 `status: current`;非 `reading` 节点还要求 `last_verified` 在 90 天内。`reading` 是低维护资料卡,观点/方法不因 90 天未验证自动降置信度。
   - **中**:命中,但节点 `stale` / 超 90 天 / 仅 tag 间接相关。
   - 关键结论找不到原始出处或收录时间时,即使命中节点,整体置信度最高为**中**。
   - **低 / 未命中**:执行**漏查防护** —— 二次放宽检索(换近义词重跑知识节点查询、放宽到
     tag 与邻接领域、扫 journal 近期记录),报告"已检索 N 个方向,未命中;主题接近的有……"。
     Meego / Base / 风神大 raw 仍只通过 `source-record` 放宽标题或显式查历史，禁止退回全文 `grep`。
     **明确区分「知识库确实没有」与「我可能没找到」**。
6. **输出格式**:先 TL;DR,再展开;每个知识库事实段落 / 列表项就近标 `[S<n>]`;末尾按
   `references/citations.md` 输出“引用”(原始出处 + 原文时间 / 覆盖 + 收录时间 + 版本 / raw_id)
   和置信度。时间敏感事实若来源明显旧,正文直接提示“可能已过期”。

## update — 更新

**触发**:子命令 `update`;或自然语言 —— "更新X""X有新进展"。

1. 定位目标节点(查 INDEX)。
2. 若用户带来新输入 → 先按 `digest` 流程摄取为 raw。
3. 冲突检测(同 `references/digest-core.md`「冲突检测」)。
4. **合并** —— 多源不一致时,以更晚的来源为准;旧值移入节点的「历史」章节并标注来源 + 日期,**不静默丢弃**。决策被取代 → 旧 `decision` 设 `status: superseded` 与 `superseded_by`。
5. 刷新 `updated` 与 `last_verified`;更新 INDEX、追加 journal。

## brief — 会前简报

**触发**:子命令 `brief`;或自然语言 —— "准备下个会""今天会议简报""会前简报"。

1. 用 `lark-calendar`(`+agenda`)取日程。日历调用失败 → 明确告知,不静默。
2. 对每个会议:提取主题、参会人。
3. 用主题词/人名查知识库(同 `search` 的检索)。
4. 每个会议生成简报:相关 `project`/`decision`/`person` 节点的 TL;DR,并按
   `references/citations.md` 给事实逐条附 `[S<n>]`,展开原始出处与收录时间。无相关条目 →
   明说"该会议在库中无相关上下文"。
5. 这是用户触发的拉取式,**不做后台推送**。

## dashboard — 工作看板

**触发**:子命令 `dashboard`;或自然语言 —— "看板""今天进展""我要长期关注 X""看板提醒 Y"。

看板文件 = 知识库数据目录下的 `dashboard.md`(与 `INDEX.md` 并列)。它是**实时视图**:📌 固定段由用户掌控,其余每次刷新重算 —— 看板不会过时。结构见 DESIGN.md §9。

**查看 / 刷新看板**:
1. `dashboard.md` 不存在 → 按 DESIGN.md §9 初始化。
2. 刷新派生内容:
   - 📌 长期关注:每个绑定了节点的关注项,从该节点拉最新 TL;DR/状态填"当前状态"列;自由文本项原样保留。
   - ⚠️ 需要关注:跑一次轻量扫描 —— 扫节点 frontmatter,标出 `last_verified` 超 90 天或 `status: stale` 的节点、未裁决冲突;手动提醒项原样保留;从 `todo.md` 派生逾期 / 临期数量与最多 3 项标题。`reading` 是低维护资料卡,不因 90 天未更新进入陈旧告警。
   - 📅 今日进展:从当天 `journal/` 渲染(本库操作 + 用户报告的进展)。
   - 更新"最后刷新"时间戳。
3. 输出看板。

看板的 `当前状态`、派生告警与今日进展若来自节点 / raw / report / journal,回显时按
`references/citations.md` 给事实加 `[S<n>]` 并列原始出处与收录时间。用户手写的 📌 / ⚠️、
`todo.md` 和 `context.md` 内容单列为“用户确认 / 本地状态”,不得伪装成 digest 引用。

**长期关注 增/删**(用户说"长期关注 X"):能定位到知识节点 → 记 `节点 id + 关注什么`;定位不到 → 存自由文本 + 提示"摄取相关资料建节点后,看板就能自动拉状态"。写入/移除 📌 段对应行。

**加今日进展 / 加看板提醒**:今日进展 → 写一行到当天 `journal/`(durable),刷新时自动渲染进 📅;用户明确说“看板提醒 / 长期关注”才写入 ⚠️ 段手动项。带时间的“明天提醒我”或一次性行动走 `references/todo.md`;“提醒我关注 Y”既无时间也无法判断长期 / 一次性时,简短询问一次。

**跨天**:📅 今日进展不独立存储,刷新时按当天 journal 渲染 —— 跨天自动重置,历史在 journal。

写操作遵守 `references/write-rules.md`。

## context — 全局工作上下文维护

**触发**:子命令 `context`;或自然语言 —— 用户要看或增删改自己的工作上下文,如"看一下我的工作上下文""更新下 context""把『主管说本季度重点是 X』记进去""我的当前重点改成 Y""删掉那条过期的约束"。

`context.md`(DESIGN.md §10)是使用者维护的全局工作上下文。**完全通过 Codex、OpenClaw 等对话式 agent 使用本 skill 的用户没法直接编辑文件 —— 必须由 agent 代为维护。**

**铁律 —— 区分「自动」与「受命」**:
- digest / search / brief / dashboard / daily / weekly 等流程中,agent **永不擅自改动 `context.md`**(它只被当「透镜」读)。
- 仅当用户**明确要求**增 / 改 / 删某条上下文时,agent 才按本节代为编辑。

**查看**:用户只想看 → 读 `context.md`,把内容回显给用户(对话式用户看不到文件)。

**维护**(用户要增 / 改 / 删):
1. 读 `context.md`(「操作前必读」已确保它存在、且按 `templates/context.md` 初始化过)。
   若是旧版四章节结构,先向用户说明可无损迁移到新版身份 / 职责 / 提醒偏好结构;用户同意后保留
   全部原条目完成一次性迁移并创建本地回滚点,不得用空模板覆盖旧内容。
2. 判断动哪个章节(`我的身份` / `我的职责范围` / `我的当前重点` / `主管方向` / `当前约束` / `交互与提醒偏好` / `背景信息`);拿不准就问用户,不擅自归类。
3. `我的身份` 只修改固定表格的值;其它章节按模板使用简短条目,变更型信息优先写 `- <YYYY-MM-DD> —— <一句话>`。**七个章节名、身份表字段与 `<!-- 指引 -->` 注释保持不动**。姓名 / 别名 / `feishu_id` 是本人匹配依据;open_id 是应用级 id,先解析为 `feishu_id`,不把 open_id 当长期主键。
4. 保持简短 —— 发现明显过期的旧条目,**提示用户**是否一并清掉,不擅自删。
5. 原子写入(temp-then-move)。
6. **回显**:把改完后相关章节的内容呈现给用户确认 —— 对话式用户没有别的途径看到结果。
7. 知识库数据目录精确暂存本次改动路径后 `git commit`(回滚点);journal 追加一行。
