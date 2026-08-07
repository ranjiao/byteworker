# byteworker · 写入规范

> 由 `SKILL.md` 路由到这里。凡要写 `raw_data/`、`knowledge/`、`reports/`、`dashboard.md`、
> `INDEX.md`、`journal/`、`context.md` 或 `todo.md`,动手前必须遵守本文件。

## 通用规则

- **标准 digest 统一走事务工具**:按 `references/digest-transaction.md` 生成临时 plan 与完整候选
  节点,通过机器协议在 `preflight` 后直接运行 `bin/digest-txn.py execute`；`execute` 内置完整校验和锁内
  复检，独立 `validate` 仅用于失败诊断。禁止为单篇业务资料在 skill 仓库写硬编码
  落库脚本;禁止绕过事务工具手算 digest hash、手拼 raw 或声称未收到 receipt 的写入已完成。
  `update`、context、dashboard、报告/IM 等非标准 digest 统一按 `references/kb-mutation.md`
  生成 plan 并调用 `kb-mutate validate/execute`；Todo 只走 Todo 工具。
- 节点文件按 `templates/node-<type>.md` 骨架;生成时**删除** `<!-- 指引 -->` 注释。
- **原子写入**:由 digest/mutation/Todo 工具执行临时文件、校验、替换与失败回滚。Agent 不直接
  修改 KB 目标文件。
- **双向 links**:写 A→B 链接,必同时在 B 的 `links` 写回 A。
- **sources / links 去重**:更新已有节点时,`sources` 与 `links` 必须去重保序;同一个
  `raw_id`、同一个 URL、同一个节点 id 不重复追加。若同源新版本产生新的 `raw_id`,可以追加新
  `raw_id`,但不要重复追加旧来源。
- **自动连边(auto-link)**:写节点 body 时扫描正文,凡出现其它节点 id(形如 `person-xxx`、
  `project-xxx`、`thinking-xxx` 等 8 类前缀)且该 id 在 INDEX 中确实存在的,自动并入本节点
  `links` 并双向写回 —— 不依赖 digest 时主动想起,避免漏连。批量修复时运行
  `bin/repair-links.sh --autolink`。
- **INDEX / journal / 回滚点**:所有持久写工具在共享 KB 写锁内按需重建 INDEX、追加 journal、
  精确暂存实际路径并创建本地 commit；已有 staged 变更或目标路径脏时 fail closed。Agent 不运行
  `git add` / `git commit`，不手工补写 journal。
- **Todo 写入**:`todo.md` 由 `bin/todo.py` 原子维护，Agent 通过统一机器协议调用;用户确认前的 digest 候选不得写入。新增 / 完成 / 延期 / 取消 / 真正发出提醒后,只暂存 `todo.md` 与本次 journal 路径创建本地回滚点。todo id 是内部键,用户侧按自然语言标题和当前对话消解。
- **命名 / 字段**:严格按 docs/development/DESIGN.md §2(命名)与 §4.1(字段)。
- **thinking 例外**:`thinking` 不创建 raw、不走 digest transaction；按
  `references/thinking.md` 生成候选，并通过 `byteworker-kb-mutation/v1` 原子写入。允许最小
  frontmatter 和自由正文，但仍维护双向 links、INDEX、journal 和本地 Git 回滚点。
- 单类节点 > 200 条 → 提示用户该类按子目录分片(暂不自动做)。

## area 主题领域的业务 / 团队边界

`area` 表示有明确适用范围的主题观察层，不表示公司级通用知识。复杂部门协作中，即使多个业务
使用相似术语或架构，各自的推进节奏、指标口径、决策权、成熟度和技术共识也可能不同。新建或
更新 `area` 时必须执行：

1. 先从原始来源确认归属：产品 / 业务（如抖音、TikTok Live）、组织 / 团队（如 CQC 数据团队）
   或个人。无法确认时不创建宽泛 `area`；保留为 `reading` / `project` 并披露归属缺口，必要时
   请用户裁决。
2. `title` 与正文 H1 使用“`<业务 / 团队 / 个人限定语>：<主题>`”；TL;DR 和概述首句再次写明
   适用范围，并明确“不代表其它业务 / 团队或公司级共识”。不能只在 tag、links 或正文深处补
   限定语。
3. 同一主题涉及多个业务时，默认分别建 area，分别保留来源、指标、状态和观点。只有来源明确
   建立跨业务治理或比较口径时，才可建立标题同样带范围的跨业务 area；相似方案不能作为合并
   共识的依据。
4. 晋升材料、个人总结或单一作者方法只代表个人时，标题带作者名，正文标为个人思考；除非有
   可核验的团队决议或规范，不得上升为团队方法论。
5. 修正已有宽泛 area 时，同时复核 TL;DR、概述、sources、links 和双向链接；发现混入其它业务
   的材料时，将它们移回各自的 reading / project / area。内部 id 可为保持引用稳定而不改名；若
   id 本身也需改名，使用 `superseded` 旧称节点保留历史跳转。

## org 组织节点的飞书架构对齐

大公司里的口语团队名、项目组名和正式组织名经常不一致。内部 `org` 节点必须尽量对齐当前飞书
通讯录组织架构，并把“正式名称核验”与“负责人确认”作为两条独立事实处理：

1. `title` 与正文 H1 优先使用飞书通讯录返回的**完整正式部门路径**（如
   `Data-抖音-内容技术`、`Data-抖音-UGC`），不自行缩写、拼接或把路径片段分别建成组织。
   若只能确认口语简称，先在正文标记“正式组织名待确认”，并询问用户；不得把简称写成已核验的
   正式名称。
2. 有组织成员的 open_id 时，运行
   `bin/byteworker run bin/resolve-users.sh --format json`，使用同一次实时查询返回的
   `department_path`；已有 person 节点的 `department_path` 与 `directory_verified_at` 可用于
   交叉核对，但它是可变的本地快照，不能压过更新的实时结果。查询为空不清除旧信息，也不据姓名、
   职位或文档内容猜部门。
3. 多个人的相同 `department_path` 可以增强名称核验，但只证明这些人在核验时属于该部门；它不
   证明谁是组织负责人。现有 org 名与实时目录路径冲突、不同成员返回不同路径，或只命中上级 / 下级
   路径时，列出差异并请用户裁决，不能静默改名或合并。
4. 每个内部 org 都应在「基本信息」记录负责人。负责人只接受：用户明确确认，或能直接证明该职责
   的权威组织来源。不得从职级、关键成员顺序、文档作者、会议主持人、发言频率或项目 owner 推断。
   一般情况下创建 / 触达 org 时询问用户；用户暂缓确认时写“负责人：待用户确认”，并保留为待核验
   事实。用户确认后记录确认日期和“用户确认”来源；飞书目录核验日期不能冒充负责人确认日期。
5. 负责人对应 person 已按 `feishu_id` 消解时建立双向 link；解析失败时只保留用户给出的姓名和
   “待解析人物”，不创建 `feishu_id: ?` 的 person。组织正式名、负责人、成员归属三类事实分别
   记录证据与核验时间，不能互相替代。
6. 修正历史随意命名的 org 时，复核 title、H1、TL;DR、基本信息、成员和双向 links。内部 id 可为
   保持引用稳定而不改；需要替换 id 时使用 `superseded` 旧节点保留历史跳转。正式组织更名或人员
   调动属于时变事实，按冲突策略保留旧值与日期，不直接覆盖历史。
7. person/org 关系必须区分三种事实：
   - **通讯录当前归属**：只写 `resolve-users` 实时返回的 `department_path` 与
     `directory_verified_at`；不得用用户确认的管理组织反写目录字段。
   - **管理职责**：谁负责哪个 org，只接受用户确认或权威组织来源，记录确认日期；负责人可以与
     当前通讯录归属路径不同，也可能只核验到更粗的祖先路径，两者并列披露。
   - **汇报关系**：谁向谁汇报单独记录来源与日期；负责人关系不自动等于直属汇报，组织父子路径
     也不自动生成个人汇报关系。
8. 用户给出账号简称、英文名、异体字姓名或拼写变体时，先用通讯录定位 open_id，再运行
   `resolve-users.sh --format json` 取 `feishu_id`；与已有 person 唯一匹配时复用原 id 和标准姓名。
   多个候选或无法唯一消解时询问用户，禁止按字符串相似度新建重复 person。
9. 飞书只返回上级/祖先部门时，不补写不存在的细粒度 `department_path`，也不把每个路径片段自动
   建成 org。用户可明确确认细粒度正式组织名、负责人或汇报关系；此时 org 中写“用户确认”，person
   中保留真实目录快照，并说明两种证据的粒度差异。目录归属与管理职责不同不等于冲突，除非双方
   都在声称同一种当前归属事实。
10. 项目协作、会议同现、周报署名、历史 sources/links 只证明历史参与或协作，不证明当前成员关系、
    组织父子层级、负责人或汇报线。用户纠正“A 不属于 B”时，修正双方当前 TL;DR、基本信息、成员
    清单和双向 links；旧材料和旧关系以带日期的历史记录保留，不删除来源，也不继续作为当前归属。

## 时间格式

严格按 docs/development/DESIGN.md §2.1。写入任何 raw frontmatter、knowledge 节点、INDEX、journal、dashboard、reports、todo 前,先把可结构化时间规范化:

- 日期写 `YYYY-MM-DD`。
- 人读时间写 `YYYY-MM-DD HH:MM`。
- 机器边界 / 群聊高水位写 `YYYY-MM-DDTHH:MM:SS+08:00`。
- ISO 周写 `YYYY-Www`。
- 不要在 skill 生成内容中写 `20260520`、`5-21`、`05/21` 等裸格式;这些只允许保留在 raw 原文正文里。

## 时间顺序

节点 body 中凡是带日期 / 时间的条目,统一按**事件发生时间倒序**排列(最新发生的在前),而不是按写入时间随手追加。

典型章节:
- `person` 的「协作历史与关键交互」。
- `project` 的「关联文档与会议 / 关键进展 / 思路与视角 / 历史」。
- `org` 的「协作历史」。
- `decision` 的「历史」。

新增条目时插入到正确时间位置;日期不明的条目放在该章节末尾并标注时间不明。

## 章节条目去重

更新已有节点时,先读原章节,再按下面的 key 去重;命中重复则跳过或合并来源,不得把同一事实
反复追加成多条。

- `project`「关联文档与会议」:同日期 / 周期 + 同 `raw_id` / `event` / `reading` / URL 视为同一条。
- `project`「关键进展」:同日期 + 同来源 + 语义相同视为同一条;同源新版本只在事实发生变化时改写
  对应条目,旧事实被推翻时移入「历史」。
- `project` / `area`「思路与视角」:同日期 + 同作者 + 同来源 + 同【主张】/【意图】内容视为同一条。
- `person`「协作历史与关键交互」:同日期 + 同 `event` / `raw_id` / `reading` 视为同一条。
- `person` 通讯录部门变化:同日期 + 同旧 `department_path` + 同新 `department_path` 视为同一条；
  只有非空新值与当前值不同时才追加，空查询不生成变化记录。
- `org`「协作历史」:同日期 + 同来源节点 / `raw_id` 视为同一条。
- `decision`「历史」:同来源 + 同状态变化视为同一条;同一决策新版本改变结论时,用
  `status: superseded` / `superseded_by` 或「历史」记录演进,不要制造同义重复 decision。

条目去重不等于丢信息:若新输入补充了同一条目的关键细节,合并到原条目并追加新 `source`;若两条
事实相互矛盾,按 digest 的冲突检测交给用户裁决。
