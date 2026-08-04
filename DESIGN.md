# byteworker 知识库 — 存储结构与字段设计

> 本文档锁定「存什么、存成什么格式、字段怎么设计」。SKILL.md 与 templates/ 按此实现。
> 来源:CEO 评审 2026-05-20(SCOPE EXPANSION 模式);2026-05-20 改为实体图模型。

---

## 0. 核心模型:实体图

知识库是一张**实体图**。不再用会漂移的「topic 分类」,而用 **8 类实体/记录/思考节点**,
知识持续累积到节点上,节点之间互相链接。

**实体(持续更新的活节点):**
| 类型 | id 前缀 | 节点上累积什么 |
|------|---------|----------------|
| 人员 `person` | `person-` | 角色、负责什么、协作历史、偏好、关键交互 |
| 项目 `project` | `project-` | 状态、范围、里程碑、风险、关联决策。**广义=有生命周期的专项/事项**(大促、事故等持续事项也归这里) |
| 主题领域 `area` | `area-` | 常青参考知识、规范、how-to、踩坑 |
| 组织 `org` | `org-` | 团队/供应商的职责、成员、对接方式、协作历史 |

**记录(产生即定型的节点):**
| 类型 | id 前缀 | 内容 |
|------|---------|------|
| 事件 `event` | `event-` | 一次会议/评审/发布的 digest 快照,定型;链接到人/项目/组织 |
| 决策 `decision` | `decision-` | 一个决策及理由/相关方/影响;可被新决策 supersede;链接到项目/事件/人 |
| 读物 / 资料卡 `reading` | `reading-` | 外部 blog/论文/wiki,以及内部路线思考/方法论/调研/技术白皮书的 digest:核心观点 + 方法框架 + 可借鉴点 |
| 思考 `thinking` | `thinking-` | 用户自己的认知、直觉、假设、推演和方案草稿；按稳定主题持续更新，以自然语言承载语义 |

**图的边:** event 链接它涉及的 person/project/org;decision 链接相关 project/event 与决策人;
project 链接成员 person、所属 area、所属 org;reading 作为资料入口链接它支撑或影响的 project/area/decision/event；
thinking 链接它讨论的 person/project/area/org/decision/reading。
查询「关于某人我都知道什么」= 对应 person 节点 + 所有链回该人的 event/decision/project。

---

## 1. 目录职责 — 逻辑与数据严格分离

byteworker 由**两个物理隔离**的部分组成。

### A. skill 仓库(纯 agent 逻辑,可进 git / GitHub)

| 文件/目录 | 存什么 |
|-----------|--------|
| `SKILL.md` | skill 行为定义(digest/search/update/brief/dashboard/todo/context/thinking/dreaming/doctor/help + 自动日报/周报) |
| `ARCHITECTURE.md` | 信息处理流程、代码分层、模块边界、依赖方向与演进约束 |
| `DESIGN.md` | 本文档:存储 schema |
| `templates/` | 8 类节点骨架模板 |
| `bin/digest-txn.py` + `lib/digest_txn.py` | digest 确定性 hash / 幂等 / 校验 / 写入事务;不含业务语义 |
| `lib/sources/` | 来源适配器注册表、`SourceBundle` 契约及 provider → transaction 兼容边界；provider payload 保持异构 |
| `bin/source.py` + `lib/source_profiles.py` + `lib/snapshot_store.py` | 统一来源 capability、Profile、capture、Bundle 构造及历史完整快照选择/差异；实例参数只在用户 KB |
| `bin/wiki.py` + `lib/wiki_explorer.py` | 按需解析飞书 Wiki 空间、完整扫描目录或选定子树、生成有限主题/页面候选；不读取页面正文、不生成 Bundle |
| `bin/digest-job.py` + `lib/digest_jobs.py` | 已确认 Wiki 页面列表的持久批次、租约、逐页 receipt 状态与崩溃恢复；不参与单页 digest transaction |
| `bin/kb-query.py` + `lib/kb_query.py` | 无持久索引的确定性候选召回、一跳图扩展与 evidence 解析 |
| `bin/kb-mutate.py` + `lib/kb_mutation.py` + `lib/kb_write_txn.py` | 非 digest 候选的版本化事务、所有 durable writer 的共享锁与回滚原语 |
| `bin/context.py` + `lib/context_view.py` | 按 intent 读取固定章节的有限 context 投影 |
| `bin/semantic.py` + `lib/semantic_policy.py` | 校验 IM 分数/阈值/reason/evidence，不保存模型输出 |
| `bin/provenance-backfill.py` + `lib/provenance*.py` | 出处 sidecar、节点证据物化及历史 raw 保守回填 |
| `bin/doctor.py` + `lib/doctor.py` + `lib/doctor_sources.py` | 按当前 DESIGN/模板/代码契约只读扫描知识库兼容性；来源契约审计独立覆盖 Profile、routine 迁移、raw/Profile 绑定、持久化 payload/record index，主 doctor 编排 INDEX/links 的确定性修复 |
| `bin/byteworker` + `bin/byteworker-launcher.py` + `lib/runtime_deps.py` | 无需 Agent 猜路径的稳定启动入口；解析 Python >=3.10、Node、lark-cli、meegle 并向子进程注入同一 runtime |
| `bin/session-preflight.py` + `lib/session_preflight.py` | shell 更新完成后，每个新 session 一次合并 KB、依赖、Todo 与自动报告设置检查；健康路径静默 |
| `bin/byteworker-cli.py` + `lib/machine_protocol.py` | 为确定性 CLI 提供 `byteworker-cli/v1` 单行 JSON envelope；不改变底层参数、业务语义或退出码 |
| `bin/update-check.sh` + `bin/update-state.py` + `lib/update_state.py` | fast-forward 自动更新、并发锁、成功/失败退避状态和独立 postflight 重试 |
| `bin/update-postflight.py` + `lib/update_postflight.py` | 代码实际更新后运行 doctor auto_fix、复扫并创建知识库本地回滚提交 |
| `bin/report-automation.py` + `lib/report_automation.py` | 自动报告首次设置状态、prompt 版本、跨日报/周报执行租约与真实运行回执；不创建宿主任务 |
| `TODOS.md` / `CLAUDE.md` | 延后项 / 仓库须知 |
| `.kbconfig` | 知识库数据目录的绝对路径(**已 gitignore,不提交**) |

launcher 只解决本机 runtime 发现与一致执行，不下载依赖、不切换登录身份，也不构成远程工具
注册表。`byteworker-session-preflight/v1` 与 `byteworker-runtime-check/v1` 都是瞬时只读回执，
不新增知识库持久化 schema；可调用工具仍由代码中的小型白名单明确列出。协议细则见
`references/machine-protocol.md`。

### B. 知识库数据目录(业务数据,用户指定,**绝不进 skill 仓库的 git**)

| 目录/文件 | 存什么 | 谁写 | 可变性 |
|-----------|--------|------|--------|
| `sources/` | 每个可重放来源一份独立 operational profile；保存 selector、capture policy 与 routine，不含凭据和抓取结果 | `source register` / `source profile-save` 事务写入 | 显式重配时更新 |
| `raw_data/` | 摄取的**逐字原文** + 溯源元数据,一次摄取一文件 | skill 写入;正文永不改写,运维 frontmatter 可更新 | 正文只增不改 |
| `provenance/` | 每个 raw 的原始定位 sidecar:文档 block / 评论 / 消息 / 妙记片段等 | digest 事务写入;受控回填可补充 | 只随对应 raw 增补/升级 |
| `knowledge/{people,projects,areas,orgs,events,decisions,readings,thinkings}/` | 8 类节点笔记,按类型分子目录 | skill 写入/更新 | 实体和 thinking 可更新；来源记录定型 |
| `journal/` | 摄取/更新/扫描事件的**时间线日志** | skill 追加 | 只追加 |
| `reports/daily/`, `reports/weekly/`, `reports/morning/` | 日报 / 周报 / Dreaming 晨报归档快照 | legacy report automation 或 Dreaming report action 写入,用户可手改 | 可覆盖同周期 |
| `reports/im/`（仅历史 KB 可存在） | 已移除 Inbox writer 留下的历史 IM 摘要 | 当前 skill 只读,用户可手改 | 禁止新建或改写 |
| `INDEX.md` | 主索引:8 类节点登记表 + 定期摄取清单 + 群聊高水位 | skill 维护,可全量重建 | 高频更新 |
| `dashboard.md` | 工作看板 —— 实时视图(长期关注 / 需关注 / 今日进展) | skill 维护/渲染 | 高频刷新 |
| `context.md` | 格式化全局工作上下文 —— 身份 / 职责 / 重点 / 约束 / 提醒偏好 / 背景 | 用户通过 agent 维护 | 手维护 |
| `todo.md` | 用户确认过的行动项、截止 / 提醒时间与完成状态 | 用户通过 agent 维护 | 高频更新 |
| `.last-routine-digest` | 上次「定期摄取」例程运行日期(一行 `YYYY-MM-DD`)—— 到期提醒据此判断 | skill 写入 | 每次定期摄取覆盖 |
| `state/wiki/` | 可重新扫描得到的 Wiki baseline / 子树目录状态；完整 JSON 不进入 Agent context | `wiki scan` 按需原子写入 | 无 TTL；仅显式扫描替换 |
| `state/digest_jobs/` | 用户已确认页面的批量 digest 运行 checkpoint、租约与逐页 receipt 定位 | `digest-job` 按需原子写入 | 跨 session 更新；可由 raw 部分 reconcile |
| `state/report_automation.json` | 自动报告的一次性设置选择、宿主线索、prompt 版本、跨任务租约和最近真实运行回执 | `report-automation` 按需原子写入 | 本机运行状态；宿主任务系统仍是真相源 |

数据目录路径由用户首次使用时指定(默认目录名 `byteworker_kb`,路径可配置),
记于 skill 仓库的 `.kbconfig`(已 gitignore)。数据目录是**它自己的独立本地 git 仓库**
(作误删/错改的回滚网,**永不配 remote**),与 skill 仓库的 git 互不相干。
数据目录含**公司机密内容**,绝不外传、绝不纳入 skill 仓库的 git。
`state/` 是本地运行状态：首次使用 Wiki 功能时才创建，并加入知识库 Git 的本地
`.git/info/exclude`；首次检查自动报告设置时也可创建。普通知识检索不扫描该目录。

### C. 真相源 vs 派生 —— 数据不变量

知识库数据按「丢了能不能恢复」分两层,这是一条**硬不变量**:

**真相源(truth source —— 丢失不可恢复,必须保护):**
- `sources/` —— 可重放数据源下一次如何抓取的唯一 operational truth。一个风神
  dashboard sheet 是一个独立来源，和一份飞书文档处于同等粒度；不同 sheet、report 选择、
  filter 口径和 cadence 不得共享隐式全局参数。
- `raw_data/` —— 正文不可变、逐字;一切知识的根。frontmatter 中 `digest_status`、`digest_targets`、`routine` 等运维元数据可由 skill 更新,但不得改写原文正文。
- `provenance/` —— 与 raw 内容 hash 绑定的来源定位证据。精确 block/comment/message locator
  可能无法仅从 raw 正文恢复,因此与 raw 一起保护;修订必须保留 `derived_from.content_hash`。
- `knowledge/` 节点 —— 可变消化产物,承载真正的知识价值;节点出错可回对应 `raw_data`
  重新消化(LLM digest,非确定性),但 `raw_data` 本身丢了就无源可回。
- `reports/` —— 日报 / 周报 / 晨报是用户可手改的归档快照;同周期可重新生成,但需保留手动备注。
  历史 `reports/im/` 同样是真相源，但 I7 后只读兼容，不得由 skill 新建、改写或删除。
- `dashboard.md` 的 📌 长期关注列表 + ⚠️ 手动提醒 —— 用户状态,只此一处保存。
- 活动中的 `state/digest_jobs/` —— 保存用户已确认的多页处理范围与未完成状态。它是本机
  session 恢复 checkpoint，不进入实体图或 Git；已成功页面仍以 committed raw/transaction
  receipt 为最终事实，任务可据此 reconcile。

### D. `sources/` — 可重放来源配置

`sources/<source_type>-<sha256(source_uid)>.json` 是用户 KB 的 operational truth，不得写进
skill 仓库。profile 有两个兼容 schema：

- `byteworker-source-profile/v2` 是通用结构：顶层固定为
  `source_type/source_uid/source_url/title/selector/capture_policy/routine`。当前支持
  `meego`、`feishu_base`、`feishu_chat`、`feishu_doc` 与 `feishu_wiki`；selector 必须与
  `source_uid` 相互校验，capture policy 只保存可重放的字段投影、上限、窗口/高水位策略、
  评论/白板策略和周期，不保存抓取结果。
- `byteworker-source-profile/v1` 仅作为现有 `aeolus` profile 的兼容 schema，保留其
  `coordinates/capture/routine` 结构；不得把 v1 当成新增来源的通用模板。

所有 profile 共同遵守：

- profile 不得含 JWT、token、cookie、密码等凭据，也不得含任何抓取结果；source URL 同时拒绝
  userinfo，以及 query/fragment 中大小写、编码和分隔符变体的认证字段；
- `profile_revision` 是规范化 profile JSON 的 canonical SHA-256；
- Meego/Base/群聊/飞书文档 profile 可由 `source profile-save --kb ... --file ...`
  校验并写入；风神仍由
  `source register` 在实时 inspect 后写入；Wiki 子树由 `wiki profile-create` 在
  `node-get` 解析真实坐标后写入；
- `source capture --kb ... --source-uid ...` 必须原样加载 profile，不接受同次 CLI 覆盖；
  变更口径必须显式保存新 profile revision 并创建 KB 本地 Git 回滚点。
- Wiki 没有 `source capture` / Bundle；其 Profile 由
  `wiki scan --kb ... --source-uid ...` 原样重放，结果只更新目录状态。
- 群聊显式 `start/end` Profile 只用于一次性窗口；`routine.enabled=true` 时必须使用
  `since_last=true`，高水位由已提交 raw 推导，可配置非负 overlap 防边界漏读。

其中 `source_type=aeolus` 的既有不变量：

- 一份 profile 精确对应一个 `region + app_id + dashboard_id + sheet_id`，稳定 ID 为
  `aeolus:<region>:<app_id>:<dashboard_id>:<sheet_id>`；
- `capture.report_selector` 显式记录动态选择全部报表或固定 report ID 子集；
- `capture.filters` 显式记录 `dashboard / explicit / merge` 和 canonical `where[]`；
- `capture.max_items_per_report`、`routine.enabled/cadence` 都属于该来源自身配置；
- profile 只能由 `source register` 在实时 inspect 校验后写入。之后
  `source capture --kb ... --source-uid ...` 原样加载，不接受 CLI 覆盖；变更口径必须重新
  register 并产生新的 `profile_revision` 和 KB 本地 Git 回滚点。

`raw_data/` 记录“这一次实际读了什么”：实际 report ID、effective filters、完整 snapshot 和
profile path/revision。它是历史证据，不反向充当下一次调度配置。旧 KB 尚无 profile 时，
非结构化来源仍可暂由最近 raw 的 `routine` 兼容运行；风神 routine 必须先迁移为 profile。
doctor 对 `sources/*.json` 逐文件复用当前 Profile validator，并按唯一 `source_uid` 检查
定期来源覆盖；它只报告迁移需要，不从 raw 自动生成 selector/capture policy，也不修改历史 raw。
历史 raw 中记录的 Profile revision 可以与当前 revision 不同，但 path/revision 必须成对存在，
且 source identity 必须与所引用的当前 Profile 一致。
- `context.md` —— 使用者主动维护的全局工作上下文;手维护、不可派生,只此一处保存。
- `todo.md` —— 用户确认后的行动状态;来源节点无法重建“完成 / 延期 / 取消”,只此一处保存。

**纯派生(derived —— 可随时丢弃,必须 100% 可重建,不必单独备份):**
- `INDEX.md` —— 可从全部节点的 frontmatter + body 首行 TL;DR、加 `raw_data/` frontmatter **确定性**全量重建(见 §6)。
- `dashboard.md` 的派生部分 —— 关注项当前状态、⚠️ 派生项、📅 今日进展,每次刷新重算。

**推论(SKILL.md「重建与恢复」据此实现):**
- 派生物永远服从真相源 —— 两者不一致时,**以真相源为准、重建派生物**,绝不反向改真相源。
- 「重建 `INDEX.md`」是一等操作,不是兜底:任何时候怀疑 INDEX 不对 → 直接全量重建。
- 灾难恢复:数据目录有独立本地 git。误删/错改 → `git restore` / `git checkout` 回滚;
  仅 `INDEX.md` 损坏/丢失 → 重建即可,无需动 git。

### E. Wiki 探索状态与 digest job

`state/wiki/<space_id>/baseline.json` 与
`state/wiki/<space_id>/subtrees/<sha256(root_node_token)>.json` 使用
`byteworker-wiki-tree-state/v1`：

- 保存 `space/scope/captured_at/coverage/tree_hash/nodes`；
- `coverage.complete=true` 才可替换旧状态；达到 `max_nodes`、被 `max_depth` 截断或任一 API
  请求失败均不覆盖；
- baseline 必须从 `space_id` 根节点列表开始，不能相信首页节点的 `has_child`；
- `added/changed/left_subtree` 是状态间可重算差异，`left_subtree` 不等于删除；
- 状态无 `expires_at`，routine 只能重扫用户保存为 Profile 的具体子树。

`state/digest_jobs/WJ-YYYYMMDD-NNN.json` 使用 `byteworker-digest-job/v1`。一个任务保存：

- 来源空间、根节点、确认时的 `tree_hash/selection_hash`；
- 页面稳定 `document_id/node_token/url/title`，不保存正文或凭据；
- 页面状态、attempt、有限时租约、`raw_id/commit/error`；
- 批大小与输入 token 粗估范围。

页面状态为 `pending/in_progress/noop/committed/blocked_dependency/blocked_conflict/
retryable_error/permanent_error/skipped`。只有 `digest-txn execute` 的 committed receipt 才能标记
`committed`；相同版本 preflight 命中才标记 `noop`。租约过期可重新领取；事务提交后、任务标记
前崩溃时，`reconcile` 只读 `raw_data` 中相同 `source_uid` 且
`digest_status=digested` 的记录恢复任务状态。

Wiki 树和 job 都不是新的正文 provider：树探索不生成 SourceBundle，被选择的页面继续逐个使用
`feishu_doc`。因此不得给 `lib/digest_txn.py` 或 `lib/kb_query.py` 增加 Wiki 私有格式。

### F. 自动报告设置状态与执行租约

`state/report_automation.json` 使用 `byteworker-report-automation/v1`，由
`lib/report_automation.py` 原子维护。它只保存 byteworker 对设置流程与真实运行结果的本地认知，
**不替代 Codex、Claude 或 TRAE 自己的任务列表**；宿主任务系统始终是是否存在、是否启用和下次
何时运行的真相源。

- `onboarding_version` 与 `decision` 共同保证首次安装和既有安装升级后只询问一次。
  `decision` 为 `unasked/prompted/configured/declined/deferred`；展示问题前先写 `prompted`，
  拒绝或稍后再覆盖为对应选择，避免每轮打扰。
- `owner_harness/environment/timezone` 记录已核验的运行宿主。`environment` 只能为 `local`；
  本地知识库不得交给 Web、云端沙箱或隔离 worktree 运行。
- `prompt_version` 表示任务 prompt 所遵循的当前执行契约。只有宿主任务已真实创建、立即试跑
  成功且报告、journal、本地 Git 回滚点均完成，才可把 `decision` 记为 `configured`。
- `daily/weekly` 分别记录用户确认的 schedule、宿主可提供时的 opaque task id、
  `last_attempt/last_run/last_success`。task id 可以为空，不得为没有公开管理 API 的宿主伪造。
- `recovery` 记录周期性缺口检查任务的 enabled、schedule 和 opaque task id；它不持有报告
  内容，也不自行唤醒，只描述已核验的宿主补偿任务。
- `active_lease` 是日报、周报和人工补跑共享的单租约，保存随机 token、kind、period、owner、
  获取时间和过期时间。未过期租约阻止同一知识库并发生成两份报告；过期后允许恢复。
- 领取 lease 时立即写 `last_attempt.status=running`；完整结束后 `last_run.status` 为
  `success/failed`，并覆盖 `last_attempt`。失败也必须清租约并保留 error code，但不得覆盖
  `last_success`；成功回执不是 digest 或报告事务本身，只记录上层流程已经检查过其真实产物。
- 补偿检查以 `kind + period` 对比 `last_success`，返回
  `due/complete/busy/disabled + should_run`。旧状态没有 `last_success` 时，最近成功
  `last_run` 作为兼容值；不得仅凭报告文件存在推断成功。
- 自动日报和自动周报每次都必须先完整执行所有已登记且启用来源的 routine digest，再生成报告；
  这条执行契约不受 `.last-routine-digest` 的七天交互提醒阈值限制。
- owner 迁移时，`release-owner` 保存 daily/weekly/recovery 的 enabled/schedule/native_task_id
  snapshot，确认无有效租约后禁用 legacy owner；`restore-owner` 只有在 Dreaming report jobs 已
  关闭时才能按 snapshot 恢复。历史 last_success 保留并导入 Dreaming，避免重复 period。

状态目录继续遵守 §1.B 的本地排除规则：写入前把 `/state/` 加到知识库
`.git/info/exclude`，不得提交、push 或进入报告事实来源。

### F.1 Dreaming 设置与 Job 状态

`state/dreaming/state.json` 使用 `byteworker-dreaming/v2`。`lib/dreaming_state.py` 负责安全
路径、权限、共享 state lock、原子 JSON 和 schema migration；`lib/dreaming_scheduler.py` 只
负责当前启停、due job、租约和回执语义。Dreaming 是可选后台控制面，不是业务知识真相源，也不
替代宿主本地任务列表。

- 状态文件缺失等同 `enabled=false`；只读 `status` 不创建状态文件。
- `state/dreaming/` 固定为 `0700`，状态、锁和 migration backup 固定为 `0600`；路径必须位于
  KB 的 `state/dreaming/` 内，拒绝绝对路径和 `..` 逃逸。
- 读取 v1 时先把原 JSON 原子写入 `state/dreaming/migrations/state-v1-<UTC>.json`，再原子替换为
  v2。未知 schema、v1 缺字段或替换失败均 fail closed；替换失败不得覆盖原 v1。
- `enable` 必须记录 `capability_tour_version/capability_tour_acknowledged_at` 和
  `runtime_notice_acknowledged_at`：前者证明已完整介绍 Dreaming 与 digest 的差异、能力、授权、
  生命周期和退出边界，后者证明用户已看到额外网络/模型/存储开销及机器需保持开机、唤醒、联网
  的提示。两个确认缺一不可；安装、升级和普通 preflight 不得自动启用或代填。
- `environment` 只能为 `local`；`owner_harness/timezone/enabled_at/disabled_at` 记录已确认设置。
- `jobs` 固定包含 `process/morning/daily/weekly/maintenance/recovery`，分别维护 enabled、schedule、
  `lease_epoch`、`last_attempt/last_run/last_success`、
  `next_attempt_at/consecutive_failures/deadline_at/blocked_by/ready_since/waiting_for_user`。
  scheduler 按 deadline、ready age 和稳定 job name 公平选择；瞬时错误 5 分钟起指数退避至
  4 小时，授权类错误等待显式 `retry-job`，不能压住其它 job。
- `grants` 初始化 `revision=0`、`im.mode=off`、`persist_finding=false`。`grant set-im` 每次变更
  revision；`all_visible` 必须确认会读取 P2P/免打扰。降级会清理被撤销范围内未晋升 spool/batch。
- `grants.actions` 分别保存 `persist_report/archive/instant_alert`，默认全部关闭。
  `grant set-actions` 是整组替换；任何 grant revision 变化会取消未 claim action，并把 claimed
  action 转入 reconcile。
- `runs/cursors/gaps/receipt_index` 由 Batch Commit Protocol 维护。queryless discovery 只能标
  `best_effort`；预算截断保存时间切片 gap，不持久化 provider page token。
- 初次启用只开启 `process/morning/maintenance/recovery`；`daily/weekly` 默认关闭，避免与现有
  `report_automation` 重复运行。
- `maintenance` 默认工作日 03:30，通过公开 doctor facade 先 scan，再执行 finding 明确声明的
  INDEX/links 确定性修复。剩余重要 error、证据/身份风险或自动化阻断项只向用户给有限元数据摘要，
  以 `DOCTOR_USER_DECISION_REQUIRED` 进入 `waiting_for_user`；不保存业务正文，不猜语义修复。
- 旧 v2 state 缺少 `maintenance` 或 capability tour 字段时，只在内存补成 disabled/未确认；
  `status` 不写回，也不因代码升级自动开启维护。下一次用户显式 enable 才记录新导览并开启该 job。
- `manage_reports=true` 前必须确认旧 scheduler owner 已释放；如果
  `state/report_automation.json` 仍声明日报或周报 enabled，迁移必须 fail closed，且不得修改
  旧状态文件。
- `active_lease` 保存随机 token、job、period、owner、fencing epoch 和过期时间。首版串行领取
  一个 job，但每个 job 的成功历史相互独立；长任务通过 token 匹配的 `renew` 延长有效租约。
- `partial/failed` 必须带稳定 error code，且不得覆盖 `last_success`；过期 lease 记录为
  `DREAMING_LEASE_EXPIRED`。
- 禁用只关闭后续 job，不删除历史 receipt、报告或 findings，也不修改现有自动报告设置。

Dreaming 运行正文、checkpoint 和后续 findings 均位于 `state/dreaming/` 或系统临时目录，继续
受 `/state/` Git 排除规则保护。任何长期知识仍必须通过现有 SourceBundle + DigestTxn；Dreaming
状态不能作为事实证据或绕过事务。

`lib/dreaming_models.py` 定义并结构校验以下瞬时契约，不负责语义正确性，也不自行持久化：

- `byteworker-evidence-batch/v1`
- `byteworker-dreaming-batch/v1`
- `byteworker-finding-bundle/v1`
- `byteworker-action-plan/v1`
- `byteworker-action-claim/v1`

这些契约的正文实例可能包含业务信息，只能位于系统临时目录或 KB 的私密 `state/dreaming/`；
仓库内只允许无业务内容的模板和测试 fixture。

I2 状态布局：

```text
state/dreaming/
  state.json
  spool/<batch_id>/<message_hash>.json
  batches/<batch_id>/
    manifest.json
    batch.json
    analysis.receipt.json          # I3 写入
    consolidation.receipt.json     # I3 写入
    batch.commit.json
```

- `process prepare` 采集并提交 `collected` manifest，只向机器输出 batch id、相对 manifest path、
  数量和 coverage，不输出消息正文。
- monitored lane 从已登记 `feishu_chat` Profile 获取 chat_id，并用逐 chat 分页形成
  complete/partial coverage；all_visible 使用 queryless search，永远是 best_effort。
- message 去重键为 `message_id + update_time/create_time`；spool 文件保存规范消息 JSON，权限
  `0600`，manifest 只保存 anchor、hash 和 `spool://` 引用。
- commit 必须已有 consolidation receipt；先 fsync `batch.commit.json`，再更新 cursor 和
  `committed_batch_id`。marker 已存在而 cursor 缺失时 recovery 只补 cursor。
- 默认 spool TTL：all_visible 24 小时、monitored 72 小时。GC 和 grant 撤销不得跟随符号链接。

I3 Finding 状态：

```text
state/dreaming/
  finding-history.jsonl   # byteworker-finding-event/v1，fsync 追加
  findings.json           # byteworker-findings/v1，可由 history 重建
```

- `process commit --input <FindingBundle>` 复验 manifest hash、batch id、grant revision 和所有
  evidence refs；FindingBundle 必须位于系统临时目录或 KB，拒绝 skill 仓库路径和超过 2 MiB 输入。
- Finding 必须有稳定 id、kind、summary、why_it_matters、confidence、uncertainties 和非空 evidence。
- `persist_finding=false` 时只写不含正文的 analysis/consolidation receipt 并 commit cursor；
  不创建 history/projection。
- `persist_finding=true` 时，事件键为 `batch_id + finding_id`；先 fsync history，再原子替换
  projection。history 保存每批 proposal delta，不保存累计快照；相同 batch 重放幂等，不同
  bundle hash 拒绝；跨 batch 同 finding id revision 递增。撤销任一 batch 后从剩余 delta 重算，
  不得残留被撤销 evidence。
- grant 降级/关闭会删除撤销 batch 的未晋升 Finding 事件并重建投影。Finding 是运行状态，不是
  knowledge/provenance，也不能作为报告或长期知识的原始证据。
- `context view --intent dreaming` 提供身份、职责、重点、主管方向、约束、提醒偏好和背景；
  I7 后不再提供独立 `inbox` intent。

I4 Action Ledger：

```text
state/dreaming/
  actions/<action_id>.json   # byteworker-action-ledger/v1，0600
  state.json.actions         # 有限状态索引，不复制正文
```

- Action kind 固定为 suppress/wait/include_report/instant_alert/todo_candidate/source_candidate/
  conflict_review/knowledge_candidate。模型提供的 `policy_result` 不可信，由 Python 重算。
- include_report、instant_alert、knowledge_candidate 分别要求对应 grant；Todo、来源、冲突必须
  用户确认；knowledge 还要求 complete monitored evidence，并强制 recapture。
- plan 必须绑定当前 lease token；claim 绑定 run/job/period/lease epoch/grant revision，返回随机
  claim token 和稳定 dedupe key。下游调用前必须 `validate-claim`。
- Ledger 不执行下游写入。Agent 使用现有公开 mutation/Todo/DigestTxn，并把 dedupe key 传为下游
  idempotency key。报告/Todo/知识要求 KB Git 中真实存在且路径匹配的 committed receipt；即时提醒
  要求 delivery id；无写动作才接受 noop。所有 receipt 的 key 必须匹配。
- 租约过期或 grant 变化后，claimed action 不重新认领，只进入 reconcile。无真实 receipt 时保持
  reconcile；找到 receipt 后可对账 committed。committed 重放相同 receipt 幂等，不同 receipt 拒绝。
- Ledger 只保存 receipt hash、status、commit 和 idempotency key，不复制下游业务正文。

I5 报告消费者：

```text
state/dreaming/
  reports/<kind>-<period>/packet.json
  state.json.report_dependencies
  state.json.report_owner
  state.json.outbox
```

- 报告窗口：morning 为前一日 20:30 至当日 08:30；当期自动 daily 为当日 00:00 至当前 tick，
  历史补跑 daily 为完整自然日；weekly 为完整 ISO 周。均按 Dreaming timezone 计算后保存 UTC。
- IM cursor 落后或 gap 与窗口重叠时，报告 job 写 `blocked_by`，scheduler 先领取独立 process
  catch-up lease；process commit 清除已覆盖 gap 并刷新 dependency，下一 tick 才领取报告。
- all_visible discovery 即使追平也只能标 partial/best-effort。存在 Dreaming 尚未支持的 routine
  provider 时 morning 可 partial，但禁止 daily/weekly owner migration。
- report packet 只包含 committed Finding 投影、coverage 和 durable KB 查询指针，不读取 spool；
  文件 `0600`。报告事实仍必须通过 citations 回到原始 evidence。
- 报告提交必须走 include_report Action claim 和 KB Mutation。生成 commit 后才能完成 action 和
  Dreaming job；delivery outbox 独立维护 pending/delivered，不以报告 commit 冒充送达。
- legacy owner 迁移先由 `report-automation release-owner` 保存 schedule/task/history snapshot 并
  禁用旧状态，再由 Dreaming 写 `report_owner.owner=dreaming` 和 migration epoch。回滚顺序相反：
  先关闭 Dreaming report jobs，宿主恢复旧任务后再 `restore-owner`。
- release/restore/manage-reports 共同持有 `state/report-owner.lock`，再按固定顺序获取各自 state
  lock；避免 restore 与 migrate 并发形成双 owner。

I6 foreground/review/shadow：

- `process once` 创建最长 2 小时的 `foreground_sessions` 单次 authorization，mode 仅为
  monitored/all_visible；后者仍需显式确认。session token 不进入 status/机器输出，commit/abort
  后关闭。foreground 不修改 enabled/jobs/persistent IM grant，也始终 `persist_finding=false`。
- foreground batch 的 source lane 为 `foreground`，另存 `collection_mode`；采集、spool、
  EvidenceBatch、gap、cursor 和 commit 与后台共用同一实现，不恢复旧 Inbox scanner。
- Finding feedback 作为 `byteworker-finding-event/v1 operation=feedback` 追加，稳定键为
  `finding_id + request_id`。生命周期为 open/snoozed/resolved/dismissed/promoted；snoozed 要求
  未来绝对时间。projection 从 proposal + feedback history 重建。
- `review` 只返回有限 Finding 摘要；`explain` 返回 Finding 和 evidence locator/coverage，不返回
  spool 正文。
- shadow 评估目录必须在 KB 和 skill 仓库之外；输入只允许 sample id、priority、slice、expected/
  selected，不接受业务文本。输出只含指标和 sample IDs，history 同样保留在私有评估目录。
- 单日数据门槛：至少 200 样本，决定/责任/风险/短回复/P2P/免打扰/低活跃/附件不可读/
  partial coverage 九个切片各至少 20 个正样本。Inbox 删除产品门槛：最近 10 个工作日均通过且
  首尾跨度至少 11 天；该状态仅作为 I7 必要条件，不自动切换路由或删除文件。

### G. 瞬时 Mutation 与语义结果契约

以下 JSON 只存在系统临时目录或 KB 工作目录，不是新的持久目录：

**`byteworker-kb-mutation/v1`**

- 顶层固定为 `operation/conflict_disposition/conflict_evidence/writes/journal/commit`。
- `operation` 为 `update/context/dashboard/report`，分别只允许
  `knowledge/**/*.md`、`context.md`、`dashboard.md`、`reports/**/*.md`。
- write 目标只允许 `context.md`、`dashboard.md`、`knowledge/**/*.md`、`reports/**/*.md`；
  其中历史 `reports/im/**` 显式拒绝；raw/provenance/sources/todo 不走此契约。
- 已有目标必须带当前 `base_sha256`；新建目标的 baseline 为空。
- mode 为 `replace`、`replace_section` 或 `replace_preserving_sections`。固定章节/可保留章节由
  validator 白名单控制，Agent 不能任意匹配标题；顶层和 write 未知字段一律拒绝。
- knowledge write 必须声明 `no_conflict/user_confirmed/revision/supersede`；后三者必须带
  `conflict_evidence`。时间较新本身不构成 revision。
- execute 在共享 KB 写锁内重新验证，按需重建 INDEX，并把目标、journal 和本地 commit 作为
  一个回滚单元。plan/candidate 禁止放入 skill 仓库。

**`byteworker-im-semantic/v1`**

- 顶层是 `schema_version + threads[]`。
- 每个 thread 的 `importance/relevance_to_user` 为 0..4 整数，必须带 `reason_codes` 和至少一组
  非空 `chat_id/window/message_ids`。
- `should_include_report` 固定为 `importance >= 3 and relevance_to_user >= 2`。
- `should_digest_kb` 还要求 reason 属于明确决策、项目状态变化、关键风险或跨团队对齐。
- validator 通过前不得写报告或触发标准 digest。

**`byteworker-context-view/v1`**

- 根据 todo/search/digest/update/brief/dashboard/report/dreaming 选择固定章节，不返回无关章节。
- 投影 12k 字符以上返回 warning，24k 以上 fail closed；完整 `context.md` 写入上限为 32 KiB。
- 不静默截断。超预算时由用户归档过期条目。

---

## 2. 命名规范

- **slug**:取标题核心关键词 → 英文/拼音 kebab-case,≤40 字符;碰撞追加 `-2`/`-3`。
- **raw 文件**:`raw_data/<YYYY-MM-DD>-<slug>.md`,`raw_id` = `raw-<YYYY-MM-DD>-<slug>`。
  若目标文件或 `raw_id` 已存在,**不得覆盖**;追加 `-2`/`-3`,或在 slug 中加入规范化周期 / revision / hash
  短后缀,直到文件名与 `raw_id` 唯一。
- **节点文件 / id**:
  - 实体:`knowledge/<类型复数>/<前缀><slug>.md`,如 `project-q2-roadmap`、`area-rec-system`、`org-data-platform-team`。
    - `person` 与其它实体同规则:slug 取姓名核心关键词(英文 / 拼音 kebab-case),id `person-<slug>`、文件名同名。**id 一经生成永不改**(仅同名碰撞才追 `-2`/`-3`)。同名 / 同人消歧不靠 id,靠 frontmatter 的 `feishu_id` 字段(见 §4.1、§4.3);**新建 person 前必须解析出 `feishu_id`**,解析不到就暂不建 person。历史遗留 `feishu_id: ?` 日后解析到了**回填该字段**即可 —— 纯字段编辑,不动 id、不改名、不级联。
  - 事件含日期:`event-<YYYY-MM-DD>-<slug>`,如 `event-2026-05-20-q2-review`。
  - 决策:`decision-<slug>`;读物:`reading-<slug>`。
- **journal**:`journal/<YYYY-MM>/<YYYY-MM-DD>.md`。
- **reports**:`reports/daily/<YYYY-MM-DD>.md`;`reports/weekly/<YYYY>-W<WW>.md`(ISO 周);
  `reports/morning/<YYYY-MM-DD>.md`。历史 `reports/im/*` 文件名保持原样，不再生成新文件。
- 单类节点 > 200 时再分子目录(TODOS)。

### 2.1 时间格式规范

知识库里所有**结构化时间**统一使用下面几种格式。原始正文(raw body)必须逐字保留,不因本规范改写;但 raw frontmatter、knowledge 节点、INDEX、journal、reports、dashboard 等由 skill 生成的内容必须规范化。

| 场景 | 格式 | 示例 | 说明 |
|------|------|------|------|
| 日期 | `YYYY-MM-DD` | `2026-05-21` | 默认格式;节点 frontmatter 的 `created` / `updated` / `last_verified`、正文条目日期、`.last-routine-digest` 均用它 |
| 带本地时间 | `YYYY-MM-DD HH:MM` | `2026-05-21 19:00` | 面向人读的正文 / journal / report 生成时间;默认 Asia/Shanghai,不写秒 |
| 完整时间戳 | `YYYY-MM-DDTHH:MM:SS+08:00` | `2026-05-21T19:00:41+08:00` | 机器边界字段,如 `raw_data.ingested`、`source_window`、群聊高水位;必须带时区 |
| 时间范围(人读) | `<start> .. <end>` | `2026-05-21 19:00 .. 20:31` | 同日范围可省略结束日期;跨日写完整日期 |
| 时间范围(机器) | `<ISO8601> .. <ISO8601>` | `2026-05-21T00:00:00+08:00 .. 2026-05-25T00:07:30+08:00` | `source_window` 等可续拉字段 |
| ISO 周 | `YYYY-Www` | `2026-W21` | 周报文件名、周报标题 |
| 月 | `YYYY-MM` | `2026-05` | journal 子目录名 |

规范化规则:
- 禁止在 skill 生成内容中写 `YYYYMMDD`、`M.D`、`5-21`、`05/21` 等裸格式;输入里出现这类周期时,消化后统一转成 `YYYY-MM-DD`。例如 `20260520` → `2026-05-20`,`5-21` 在已知年份为 2026 时 → `2026-05-21`。
- `digest_period` 若表示日期周期,统一写 `YYYY-MM-DD`;若表示 ISO 周,写 `YYYY-Www`;确实不是日期(如版本号 / 阶段名)才保留原样并在正文说明。
- `INDEX.md` 的 `last_verified`、定期摄取清单「上次摄取」、群聊摄取进度「已摄取至」必须使用上表格式:日期源用 `YYYY-MM-DD`,群聊高水位用完整时间戳。
- 节点 body 中带时间的条目开头优先使用 `- YYYY-MM-DD ...`;若需要具体时间,写 `- YYYY-MM-DD HH:MM ...`。`思路与视角` 固定为 `- 【主张|意图】<作者> · YYYY-MM-DD —— <内容>`。
- journal 行以 `- HH:MM ...` 开头,文件路径已提供日期;若引用外部事件发生时间,正文里仍写完整 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`。
- 报告顶部 `生成时间` 用 `YYYY-MM-DD HH:MM`;`范围` 用人读时间范围。

---

## 3. raw_data/ — 原始输入

每次摄取写一个文件,正文逐字保留,**不做任何改写/删减**。frontmatter 是该 raw 的运维元数据,允许在 digest 完成、失败重试、纳入 routine 时更新 `digest_status` / `digest_targets` / `routine` 等字段;这不改变 raw 正文。

```markdown
---
raw_id: raw-2026-05-20-q2-roadmap-review
ingested: 2026-05-20T14:30:00+08:00
source_type: feishu_doc | feishu_minutes | feishu_meeting | feishu_chat | meego | feishu_base | aeolus | web | local_md
source_uid: doxcnxxx / wiki_token / minute_token / URL / 本地绝对路径
source_revision: "12"                       # 可选:飞书文档 revision_id / 外部 etag / git commit 等来源版本
source_profile_path: sources/aeolus-<sha256>.json # 由 profile 抓取时记录
source_profile_revision: sha256:<hex>       # 本次使用的 canonical profile hash
source_project_key: proj_xxx                # meego:空间 project_key
source_base_token: bascnxxx                 # feishu_base:真实 base token,不使用 wiki token
source_table_id: tblxxx                     # feishu_base:明确数据表
source_view_id: vewxxx                      # meego / feishu_base:保存视图
source_fields:                              # meego 字段 key / Base field ID 或精确名称
  - status
  - owner
source_region: <region>                     # aeolus:本次实际坐标
source_app_id: <app_id>                     # aeolus
source_dashboard_id: <dashboard_id>         # aeolus
source_sheet_id: <sheet_id>                 # aeolus
source_report_ids:                          # aeolus:固定读取的 report 子集
  - <report_id>
source_filter_mode: dashboard               # aeolus:dashboard | explicit | merge
source_where_filters:                       # aeolus:explicit / merge 的 canonical JSON
  - '{"dimMetId":<dim_met_id>,"name":"<field_name>","op":"in","val":["<value>"]}'
digest_period: 2026-05-20                   # 可选:滚动文档的周期;日期 / ISO 周需规范化
payload_schema: byteworker-payload-v1       # 新事务写入的组件组合 hash 规范
payload_components:                        # 本次实际摄取组件:name|kind|sha256
  - body|body|sha256:<hex>
  - comments|comments|sha256:<hex>
  - whiteboard:doxxx|whiteboard|sha256:<hex>
body_hash: sha256:<hex>                     # feishu_doc:本次实际摄取正文的 hash
comment_hash: sha256:<hex>                  # feishu_doc:canonical comments 的 hash
comments_status: complete | partial | unavailable  # feishu_doc:评论覆盖状态
comment_count: 8                            # feishu_doc:本次完整快照中的评论卡片数
comments_latest_at: 2026-05-20T14:25:00+08:00 # feishu_doc:最近评论/回复时间
whiteboard_hash: sha256:<hex>               # 可选:全部已摄取白板 component 的组合 hash
embedded_whiteboards: 2                     # 可选:实际纳入 payload 的内嵌白板数
whiteboards_status: complete | partial      # 有白板时必填
content_hash: sha256:<hex>                  # 本次实际摄取 payload(正文 + 评论等)的 hash
digest_key: feishu_doc:doxcnxxx:2026-05-20:sha256:<content>
source_url: https://<feishu-url>           # 用户可打开的原始链接;本地 md 则填原路径
source_title: Q2 路线图评审会
digest_status: pending | digested | failed
routine: weekly                            # 可选:会定期更新的源(滚动周报/群聊)纳入定期摄取后才有
digest_targets:                            # 本次摄取触达的所有节点 id
  - event-2026-05-20-q2-review
  - decision-q2-scope
  - project-q2-roadmap
related_source_urls:                       # 可选:同一会议簇 / 资料簇中已确认相关的其它原始链接
  - https://<meeting-doc-url>
  - https://<minutes-url>
---

# Q2 路线图评审会

<逐字原文 / lark-minutes 纪要+逐字稿 / lark-doc 文档正文,原样粘贴;
feishu_doc 随后附 canonical 文档评论原始快照>
```

**幂等键与重复摄取**:
- `source_uid` 是规范化来源主键:飞书文档优先用 `document_id` / wiki token,妙记用 minute token,
  群聊用 `source_chat_id`,Meego 保存视图用 `meego:<project_key>:<view_id>`,多维表格视图用
  `feishu_base:<base_token>:<table_id>:<view_id>`,风神看板用
  `aeolus:<region>:<app_id>:<dashboard_id>:<sheet_id>`,外部网页用规范化 URL,本地文件用绝对路径。
- `source_revision` 记录来源版本:飞书文档用 `revision_id`;无明确版本时可为空,以 `content_hash`
  判重。
- `source_url` 是**用户可点击回原始资料的链接**。飞书文档 / 妙记 / 日历会议 / 外部网页必须尽量
  保留;新摄取中只要来源本身可打开就是必填。可以去掉无意义的 `from=` 等跟踪 query,但不得丢失
  能打开该资源的主链接。本地文件填绝对路径。`source_title` 对有标题的来源必填;
  群聊使用 `source_chat_name`。
- `related_source_urls` 只放已确认与本次 raw 同属一场会议 / 一组资料的其它原始链接,例如会议妙记
  对应的投屏文档、日历日程链接,或会议文档对应的妙记。找不到就不写,不得臆造。
- `content_hash` 取**本次实际摄取 payload**的 SHA-256。普通来源的 payload 就是正文;飞书文档
  payload 是本次选定正文 + 纳入 raw 的 canonical 评论快照 + 实际读取的白板 / 表格等组件。
  新事务写入使用 `byteworker-payload-v1`:每个 component 先按自己的 mode 得到 bytes,再按稳定
  `name` 排序,用 component name 与内容长度作边界后组合 SHA-256,避免简单字符串拼接歧义。
  `mode=verbatim` 逐 byte hash;`mode=canonical-json` 使用 UTF-8、key 排序、紧凑 JSON且不含抓取
  时间。滚动周会的 `body_hash` 只 hash 被选中的周期正文,不是整篇文档;会议簇按合并后的实际
  component 计算。
- `feishu_doc` 必须额外写 `body_hash`、`comments_status`;评论完整时写 `comment_hash` /
  `comment_count` / `comments_latest_at`。canonical 评论快照包含全部评论(包括已解决)、完整回复链、
  作者 / 时间 / 解决状态及可取得的 relation 锚点,放在 raw 正文的独立章节。`comment_hash`
  不包含抓取时间。评论接口不可用 / 分页不完整时分别写 `unavailable` / `partial`,不得伪造空
  评论 hash;历史 raw 缺这些字段只表示当时未记录评论覆盖。
- 正文中的内嵌 whiteboard 随当前文档摄取:只读取结构化节点 JSON 作为 raw 证据 component，
  不抓取或分析预览图片。全部 token 的结构 JSON 成功读取才写 `whiteboards_status: complete`;
  任何缺失写 `partial`。仅靠渲染外观才能成立的语义不持久化；白板画出架构不证明系统已上线。
- `digest_key` 由 `source_type + source_uid + digest_period/source_window + content_hash` 组成,用于
  判断完全重复摄取。新格式固定为
  `source_type:source_uid:digest_period-or-window-or--:content_hash`;评论或白板任一 component
  变化都可独立触发新版本。普通非滚动文档用 `-` 占周期位;群聊使用 `source_window`。
- 完全相同 `digest_key` 已存在且 `digest_status: digested` → 本次 digest 必须 no-op,只向用户说明
  已摄取过,不得重复写 raw / 节点 / journal。
- 同一 `source_uid + digest_period/source_window` 但 `content_hash` 不同 → 视为同源新版本,新写一个
  raw(唯一 `raw_id`,不覆盖旧 raw),并按 digest 流程更新已有主记录与实体节点。
- 同源同内容但历史 raw 缺少 `digest_key` 字段时,用 `source_uid/source_url + digest_period +
  content_hash` 近似比对;新事务还兼容比较旧 `body_hash` / `comment_hash` /
  `whiteboard_hash` 以及历史“组件末尾补换行后直接拼接”的组合 hash。命中则按已摄取处理,可只
  补运维 frontmatter 字段,不得改 raw 正文。旧 raw
  缺少 `payload_schema` / `payload_components` 是合法历史状态,不做启动时全库迁移。

**标准 digest 写入事务**:`bin/digest-txn.py` 在 Agent完成依赖判断、冲突裁决和完整候选节点后,
一次性校验并写入 raw/节点/INDEX/journal,成功时 raw 可直接落为 `digest_status: digested`;
因为任何文件在全部候选校验通过前都不可见,失败会恢复事务前快照。手工/旧流程若先落 raw 再
消化,仍使用 `pending → digested|failed`;两种状态语义兼容。
新增单来源优先使用 `byteworker-source-bundle/v2` + `digest-plan/v2`：plan 只引用 bundle，
不得复制 `source` 或 `provenance.anchors`；bundle 是来源身份、外部 component、覆盖度、锚点、
provider metadata 和可选 record index 的唯一交接契约。`digest-plan/v1` 保持只读兼容；
新增多来源使用 `digest-batch-plan/v2`，每个 input 只引用一个 Bundle，不复制 source 或
anchors；`digest-batch-plan/v1` 保持只读兼容。batch
不引入事务数据库：仍用 `base_sha256` 乐观基线 + Git 内短时写锁，拿锁后复验，一次重建 INDEX
并生成一个本地 commit。标准事务强制 provenance；update 默认保留既有来源、证据和正文语义，
有意删除必须在临时 plan 中显式授权并说明理由。

`SourceBundle` 不定义统一正文 AST，也不要求飞书文档、群聊、妙记、Web、本地资料、
Meego、Base、风神共享抓取代码结构。
component 使用 `verbatim` 或 `canonical-json`：`verbatim + json_pointer` 只能选择 JSON
字符串并把其 UTF-8 bytes 逐字写入 raw；`canonical-json + json_pointer` 对选中值做规范 JSON
序列化。这样 lark-cli 等 wrapper 可以原样留在临时 artifact，而不要求 Agent 手工提取正文。
operation adapter 或宿主工具负责 transport；Bundle adapter 负责 provider 输入、覆盖度和
identity 校验，只需输出同一 bundle envelope。事务核心只处理 component bytes、hash、幂等和写入；旧 transaction source 的
provider 特例集中在 `lib/sources/transaction_bridge.py`，不得重新散回 `digest_txn.py`。
Bundle 从文件重载后还必须经 registry 调用 provider adapter 的 `validate_bundle`；Meego、
Base、风神从唯一 records snapshot 重新派生并比较 identity、坐标、anchor、record index 与
`snapshot_hash`，不能把通用 schema 通过当作 provider 一致。通用 bundle request 对结构化
capture 只接受 `capture_path`，不接受与路径并存的内联副本。
最终 Source 模块边界、领域模型和兼容删除条件见 `ARCHITECTURE.md` §4.3、§8.3。

**`feishu_chat` 变体**:群聊摄取按「群 + 时间窗」进行,**同一群可多次增量摄取**。
frontmatter 不用 `source_url` / `source_title`,改用 `source_chat_id`(oc_xxx)、
`source_chat_name`(群名)、`source_window`(本次摄取的消息时间窗,**完整 ISO8601 起止**,
如 `2026-05-15T00:00:00+08:00 .. 2026-05-21T18:00:00+08:00`)。`source_window` 的结束点
即该群「上次处理到哪」的**高水位** —— `bin/pull-chat.sh --since-last` 扫 `raw_data/` 取该
`chat_id` 最近一次 `source_window` 的结束时间,据此续拉下一窗口。`raw_id` 的 slug 取群名 +
窗口标识。正文为该窗口的逐字消息(发送人 + open_id · 时间 · 内容,原样)。

**`web` 变体**:外部读物(blog / 论文 / wiki)。`source_url` 填文章链接(本地 PDF 则填路径),
`source_title` 填文章标题。正文为宿主 agent 抓取/读取到的文章正文。

**回答引用读取约定**:`raw_data` 是用户可见知识库回答的引用真相源。任何来自节点 / 报告 /
journal / dashboard 派生内容的事实,回答时都要沿 `sources` / 来源索引回到 raw,读取
`source_title` / `source_url` / `source_type` / `ingested` 以及 `digest_period` /
`source_window` / `source_revision`,按 `references/citations.md` 输出论文式引用。
`ingested` 是 byteworker 的收录时间,不得拿节点 `created` / `updated` / `last_verified`、
raw 文件名或 git 时间替代。历史 raw 缺字段时必须明确披露,不能猜测;关键结论缺原始出处或
收录时间时置信度最高为中。

### 3.1 结构化保存视图的大规模摄取

Meego / Base 保存视图及风神 dashboard sheet 含大量记录/报表结果时,采用
“**一份全量快照、逐稳定单元差异、少量知识晋升**”模型:

风神的运行边界固定为 byteworker 自带的最小只读 HTTP 客户端：只允许 dashboard/sheet
发现、dataset 字段读取、report 保存态配置读取和 VizQuery 查询，不接入创建、修改、发布或
权限申请接口，也不依赖其它 CLI。用户态 Titan Passport、ByteCloud JWT、Bearer token 或
服务态 client credentials 只从进程环境 / secret manager / 仓库外 `0600` 私密文件读取；
只在内存中交换和缓存，不得进入 skill 仓库、知识库、raw、diff、provenance、日志或命令参数。
定时任务优先使用单独授权、可轮换的服务态凭据；服务身份与个人身份的资源权限必须分别验证，
不能因为个人能打开看板就推断服务身份也能读取。

- 每次摄取必须完整分页并把规范化 `snapshot` 作为一个 raw component；它是本次看板事实的
  原始证据,不能只保存摘要、变更行或单页结果。
- 结构化字段中的 URL 必须在 snapshot/hash 之前剥离一次性登录 token、access token、签名等
  敏感 query 参数；脱敏计数进入 capture 诊断，但凭据值不得进入 raw、diff、provenance 或日志。
- Meego / Base 每条记录、风神每个 report 用稳定 ID 建 exact provenance anchor。相邻完整
  快照可用 `source diff` 按 ID 生成
  `baseline / added / changed / left_view`；差异是可重算的派生物,不是新的权威真相源。
- `left_view` 只表示记录不再出现在当前保存视图中,**不等于工作项被删除或取消**。需要删除语义
  时必须回权威来源另行确认。
- Meego / Base 对状态、负责人、优先级、排期等结构化字段具有优先权；风神对其明确口径下的
  报表结果具有优先权，但 query 时间不等于底层数据更新时间；文档、会议、群聊对理由、
  讨论过程和生效决策具有优先权。两者冲突时保留各自出处,不让摘要覆盖来源状态。
- 普通需求及日常状态变化只留在 raw + provenance；满足下列门槛才进入实体图:
  长期持续且需跨来源追踪 → `project`，明确生效选择 → `decision`，评审/发布/事故等时间事实 →
  `event`，跨多条需求反复出现且稳定的能力/风险主题 → `area`。**禁止一条需求一个节点或一个
  person**；人员只在其身份/观点/协作关系本身具有长期知识价值时创建或更新。
- 首次快照建立一张代表保存视图的 `reading` 主记录；后续同源快照更新同一主记录，并只检查
  差异记录/报表是否达到晋升门槛。Meego / Base 字段投影写入 `source_fields`；风神把 report
  子集和筛选策略写入 `source_report_ids / source_filter_mode`。后续例行摄取保持一致；投影、
  report 子集或筛选策略调整视为显式的新口径版本并在主记录中说明。
- 普通记录虽不进入实体图，仍必须可确定性查询。`kb-query source-record` 先按 raw frontmatter
  的 `source_type / source_uid / ingested` 选每个来源的最新完整快照。新 Bundle 若提供
  `record_index`，事务会把它作为 `byteworker-record-index/v1` 派生 JSON 段随 raw 保存，
  query 优先使用该 provider-neutral projection；旧 raw 再回退解析 provider snapshot。
  按 Meego `work_item_id` / Base `record_id` / 风神 `report:<report_id>` 精确查找，或在
  Python 内对标题做归一化和有分数的模糊匹配。输出只包含有限条完整记录及 raw / exact anchor
  溯源，不把整个大 raw 交给 Agent。
  历史快照只能显式请求，并必须标明不是当前最新版本；该查询是无持久索引的可重算派生能力。

### 3.2 provenance/ — 原始位置 sidecar

`provenance/<raw_id>.json` 使用 `byteworker-provenance/v1`。它不改写历史 raw,而是在旁路保存
“这条事实在原系统的哪个位置”,至少包含:

- `raw_id` / `raw_path` / `derived_from.content_hash` / `generated_at` / `enrichment`;
- `source` 的类型、标题、可打开 URL 和 `ingested`;
- `anchors[]`:稳定 `anchor_id`、`kind`、`precision`、`locator`、可选 `open_url` /
  `fallback_url`、`source_time`、作者和短 quote。

`kind` 可表示 `source`、`doc_block`、`doc_comment`、`doc_reply`、`chat_message`、
`chat_thread`、`minutes_segment`、`meeting`、`meego_workitem`、`base_record`、`aeolus_report`、
`web_section`、`whiteboard_node` 或
`local_span`。`precision` 只有四级:

- `exact`:本次抓取保留了原系统稳定 id,可精确打开;
- `refetched`:为历史 raw 受控重拉同版本 / 同窗口后补得;
- `source_only`:只能回到整份原始资料;
- `unresolved`:已知有来源但尚不能可靠定位。

sidecar 的 `source` anchor 必须存在。正文、评论和聊天抓取器应尽量在抓取当下保留 block id、
comment/reply id、message/thread id;不得靠标题、文件名或模糊文本伪造 `exact`。来源变化导致
无法证明仍是同版本时,只能标 `source_only` / `unresolved`。

**`routine` 字段(兼容字段)**:滚动文档、群聊等没有独立 profile 的来源，经用户确认后仍可在
raw frontmatter 加 `routine: weekly`。有 `sources/` profile 的结构化来源以
`profile.routine` 为唯一真相源；raw 中旧 `routine` 不得覆盖 profile。INDEX 优先扫描
profiles，再为没有 profile 的旧来源兼容扫描 raw。详见 SKILL「定期摄取」。

---

## 4. knowledge/ — 节点笔记

### 4.1 通用 frontmatter

```yaml
---
id: project-q2-roadmap
title: Q2 产品路线图
type: person | project | area | org | event | decision | reading | thinking
tags: [roadmap, q2]
status: current | stale | superseded         # 实体常为 current/stale;记录可 superseded
created: 2026-05-20
updated: 2026-05-20
last_verified: 2026-05-20                     # 新鲜度判断依据(看板 ⚠️ 段用)
superseded_by: decision-xxx                   # 仅 status=superseded
sources:                                      # 溯源:raw_id 或飞书原链接,≥1 条
  - raw-2026-05-20-q2-roadmap-review
primary_source: raw-2026-05-20-q2-roadmap-review # 主记录必填;实体节点有明确主资料时填写
primary_source_url: https://<feishu-url>       # 由事务从 primary raw 物化
links:                                        # 图的边,双向维护(写 A→B 同时在 B 写回 A)
  - person-zhang-san
  - area-product-planning
  - event-2026-05-20-q2-review
---
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | ✓ | `<前缀><slug>`,全局唯一 |
| `type` | ✓ | 8 类之一,决定子目录与 body 结构 |
| `tags` | ✓ | 自由二级标签,承载角色特异性(数据集名、渠道、技术栈…);优先复用已有 tag |
| `status` | ✓ | `current` / `stale` 疑似过期 / `superseded` 已被取代 |
| `created`/`updated`/`last_verified` | ✓ | 创建 / 最后修改 / 最后被新输入或人工确认的日期,格式固定为 `YYYY-MM-DD` |
| `superseded_by` | ✗ | 退役时指向取代它的节点 |
| `sources` | ✓ | 溯源根,指回 raw_data 或飞书链接 |
| `primary_source` | 主记录 ✓ / 实体可选 | 节点最主要的 raw_id,必须同时位于 `sources` |
| `primary_source_url` | 有可打开来源时 ✓ | 由 `primary_source` 对应 raw 的 `source_url` 物化 |
| `links` | ✗ | 关联节点 id,**双向维护**;id 前缀即对端类型;body 中提及的已存在节点 id 自动纳入(auto-link,见 SKILL.md 写入规范) |
| `feishu_id` | △ | **仅 `person`**:该人飞书英文 id(企业邮箱 `@` 前缀),全局唯一 —— person 实体消解的主键、用于消歧同名。**只是一个字段,不参与 id / slug**(id 规则见 §2)。新建 person 前必须由 `bin/resolve-users.sh` / lark-contact 解析;解析不到就先不建 person,只在事件正文保留姓名 / open_id 并报告待解析。历史遗留的 `?` 允许后续回填,但不得再新增 |
| `enterprise_email` | ✗ | **仅 `person`**:本次用户态通讯录查询返回的企业邮箱；不可见或为空时省略，不用个人邮箱冒充 |
| `department_path` | ✗ | **仅 `person`**:飞书通讯录返回的当前部门路径字符串。它是可变的当前目录属性，不是稳定 org id；为空时省略，不据姓名或正文猜写 |
| `directory_verified_at` | 新建/更新 `person` ✓ / 未触达历史节点兼容 ✗ | **仅 `person`**:本次 `lark-contact` 核验时间，使用带时区 ISO8601。person 候选每次写入都必填；未被本次事务触达的历史节点可缺失，等后续真实查询再回填 |

`thinking` 使用更轻的 frontmatter 契约：只强制 `id/title/type/status/created/updated`；
`status` 仅为 `effective|inactive`。`tags/sources/links/last_verified` 均可选，且不要求
`primary_source`。`effective` 只表示这是用户当前认可的思考，不表示其中命题是客观事实。

> 不再有 `topic` 字段——领域结构由 `area`/`org` 节点 + `links` 承载,topic 治理问题消解。

### 4.2 body 结构(按 type)

所有类型 body 首行统一 TL;DR(查询先返回它):
```markdown
# <title>

> **TL;DR:** <一句话摘要>
```

**主记录来源链接要求**:`event` / `reading` 这类由 digest 直接生成的主记录,正文里必须给出
用户可点击的原始来源链接,不能只在 frontmatter `sources` 里放 `raw_id`。优先写在:
- `event` 的「事件信息」:列出原始文档 / 妙记 / 日历日程 / 会议文档链接;会议文档未找到时只写
  “会议文档:未找到”或省略,不得臆造。
- `reading` 的「来源」:列出原文链接、作者、发布日期、类型。
实体节点(`project`/`org`/`person`/`area`)被本次 digest 更新时,若有「关联文档与会议」等来源章节,
也应追加标题 + 日期 / 周期 + 节点 id / raw_id + 原始链接,按事件发生时间倒序去重。

**节点内事实证据要求**:

- 由原始资料抽取的关键事实、状态、数字、日期、决定、风险、负责人、行动项和第一方观点,
  在对应句子末尾用 `[E1]`、`[E2]` 逐条绑定;同一证据可复用。
- `[E<n>]` 是**节点内持久证据编号**,映射到 `raw_id + anchor_id`;digest 事务确定性生成末尾
  `## 证据` 表,展示原始链接、定位、原文时间、raw 收录时间和精度。不得手工伪造表格。
- `[S<n>]` 是回答 / 报告在当次输出中按首次出现顺序生成的动态引用,不写回节点。
  查询时优先沿节点 `[E]` 精确取证;历史节点无 `[E]` 时仍沿 `sources` 回 raw,但要降低定位精度。
- 纯结构标题、链接关系、明确标注的 Agent 建议不强制 `[E]`;推断仍需引用其事实依据并保留
  【推断】标签。

**`person`(实体)** —— 在 §4.1 通用 frontmatter 之外额外带 `feishu_id`，并保存可选的
`enterprise_email`、`department_path` 与每次通讯录核验时间 `directory_verified_at`。
```markdown
## 基本信息        <!-- 角色 / 当前所属团队 / 对接方式；注明通讯录核验日期 -->
## 负责什么
## 协作历史与关键交互  <!-- 带时间条目按事件发生时间倒序；组织变化保留旧归属 -->
## 立场 / 利益 / 动机   <!-- 跨讨论沉淀的立场倾向 / 核心诉求 / 行为逻辑;须有证据,见 §4.5 -->
## 偏好 / 风格 / 注意点
## 关联节点
```

`department_path` 表示“查询时的当前通讯录部门”，不是永恒事实。重复 digest 再次解析到同一人时：

- 查询结果不为空且与节点一致 → 只刷新 `directory_verified_at`；
- 查询结果不为空且发生变化 → 更新当前 `department_path`，在「协作历史与关键交互」追加一条
  带变更日期的旧部门 → 新部门记录；
- 查询结果为空或跨租户字段不可见 → 不清空已有非空值，不把空值解释为离职或调动；
- 只有 `department_path` 能明确映射到已有 `org` 节点时才建立 link；不得按路径片段自动批量
  创建组织树。

**`project`(实体,广义专项/事项)**
```markdown
## 关联文档与会议   <!-- 该项目被讨论/提及的主要文档/会议/群聊(标题+日期+链接);按事件发生时间倒序、持续追加去重 -->
## 目标
## 关键策略
## 关键进展         <!-- 带日期,按事件发生时间倒序;含里程碑、关键决策、状态变化 -->
## 问题            <!-- 当前待解决的问题/阻塞 -->
## 风险            <!-- 潜在风险 -->
## 成员 / 相关方     <!-- person 链接 -->
## 思路与视角        <!-- 各方对本项目的主观思路/想法/打法/意图;第一方陈述,带日期带作者,标【主张】/【意图】,见 §4.6 -->
## 历史             <!-- 目标/策略被推翻时旧值移入,标来源+日期;按事件发生时间倒序 -->
```
> 一个项目会被多个文档/会议反复讨论:每次 digest 涉及该项目,都要把新来源追加进
> 「关联文档与会议」,并刷新 目标/关键策略/关键进展/问题/风险。**无信息的章节留空。**

**`area`(实体,主题领域常青知识)**
```markdown
## 概述 / 定义
## 关键知识点
## 规范 / 流程 / how-to
## 踩坑 / 注意事项
## 思路与视角        <!-- 各方对本领域的主观思路/想法/判断;第一方陈述,带日期带作者,标【主张】/【意图】,见 §4.6 -->
## 相关节点与外部链接
```

**`org`(实体,组织/团队/供应商)**
```markdown
## 基本信息        <!-- 内部团队 / 外部供应商;职责 -->
## 关键成员         <!-- person 链接 -->
## 对接方式 / 流程
## 协作历史         <!-- 带时间条目按事件发生时间倒序 -->
## 关联项目
```

**`event`(记录,产生即定型)**
```markdown
## 事件信息        <!-- 时间 / 类型:会议|评审|发布|群聊讨论窗口 / 参会人 -->
                   <!-- 来源链接:原始文档 / 妙记 / 日历日程 / 会议文档 URL;若会议文档找不到,不要编造 -->
## 议程与讨论
## 结论
## 参与方立场分析   <!-- 各关键参与方的立场/动机/对决策态度;须基于证据,标【观察】/【推断】,见 §4.5 -->
## 重点事项        <!-- 和用户本人相关、重点关注项目、重要人物观点，以及其他在context.md里面要求关注的重点事项 -->
## 待办事项        <!-- 责任人 + 截止日期 -->
## 衍生与关联       <!-- 产生/更新的 decision、涉及的 project/person/org -->
```

**`decision`(记录,可被 supersede)**
```markdown
## 决定了什么
## 理由 / 背景
## 决策人 / 相关方
## 影响范围
## 当前状态        <!-- 生效中 / 待执行 / 已被取代 -->
## 关联节点         <!-- project / event / person -->
## 历史             <!-- 带时间条目按事件发生时间倒序 -->
```

**`reading`(记录,读物 / 资料卡 / 思路)**
```markdown
## 来源            <!-- 链接 / 作者 / 发布日期 / 类型:blog|论文|wiki|内部路线思考|方法论|调研|技术白皮书|复盘 -->
## 核心观点         <!-- 逐条提炼资料的关键观点、论点、方法、证据 -->
## 可借鉴点         <!-- 对工作的潜在启发(「思路」角度);无则留空 -->
## 相关节点         <!-- links;内部资料通常连到影响的 project/area/decision/event -->
```
> `reading` 是资料本身的 digest:外部读物通常弱相关于工作,默认**一篇文章一个 `reading` 节点**,
> 不走 event/decision 扇出;内部路线思考 / 方法论 / 调研 / 技术白皮书则以 `reading`
> 作为主记录,同时可按内容扇出明确 decision、更新相关 project/area/person/org。
> `reading` 低维护(观点不会像项目状态那样过期),`status` 基本恒为 `current`,不进看板陈旧告警。

**`thinking`(持续更新的自然语言思考)**

正文只要求标题和非空自然语言，不要求 TL;DR 或固定章节。它可以承载用户自己的认知、直觉、
假设、推演、方案和问题；推荐用【事实】/【用户判断】/【推断】/【建议】区分语义，但不把这些
标记固化成字段。同一稳定主题更新同一节点，当前正文可直接重写，本地 Git 保存历史版本。
整篇不再认可时设为 `inactive`；正式拍板另建 `decision`。

### 4.3 一次摄取的产出(digest 扇出)

一次摄取(raw)按下面的**形状**扇出成多个节点 —— 这是实体图的生长方式:
1. **必产 1 个记录节点**:会议 / 群聊窗口 → `event`;外部读物、内部路线思考 / 方法论 / 调研 / 技术白皮书 → `reading`。
2. **抽取 N 个 `decision`**:输入中每个明确决策抽成独立节点。外部读物默认不走此步;内部资料型 `reading` 若包含明确生效的选择 / 原则 / 边界,可以抽 `decision`。
3. **创建或更新实体节点**:输入实质涉及的 person/project/org/area —— 不存在则建,已存在则
   走**实体消解**更新(建前在 INDEX 比对;`person` 优先按 `feishu_id`,见 §4.1)。
4. **全部互链** `links`(双向),并登记进 raw 的 `digest_targets`。

> Meego / Base / 风神保存视图的“必产 1 个记录节点”是代表整个视图/看板口径的同源 `reading`,不是每条记录/报表各产
> 一个节点。首次快照的数百条 baseline 记录也不自动变成数百个 `project` / `event`；后续按
> §3.1 的差异和 `references/semantic-policy.md` reason/evidence 门槛选择性更新实体图。

> 扇出的**行为细则**是 digest 流程、不在本文件:各 `source_type` 的差异、群聊强过滤与增量
> 语义、会议簇合并、实体消解的同名陷阱、立场与思路视角的沉淀 —— 见 `SKILL.md`「digest」
> 与 `references/digest-*.md`。本节只锁定扇出的形状。

### 4.4 什么该进知识库

**该存:** 决策与理由、项目/事项状态、常青参考知识、会议结论与待办、协作关系、外部读物与内部资料的观点 / 方法框架 / 可借鉴点。
**不该存:** 一周后即失效且无留存价值的琐碎、纯寒暄。
边界不清则 agent 高亮问用户,不静默丢弃也不硬塞。

**重度定量表格**(大型数据表 / 明细表):不强行复刻进 md 节点 —— 节点存
**结论 / 趋势 / 口径**,明细留在原文档 / 原表格,用「关联文档与会议」或 `sources` 链接回去。

### 4.5 参与方立场分析(书写准则见 references)

§4.2 已定义 `event` 的「参与方立场分析」章节:只对明确影响决策、承担责任/风险或拥有审批权的
关键参与方分析立场,
结论同步沉淀进相关 `person` 的「立场 / 利益 / 动机」章节。它从会议发言**推断**而来(observed)。

**怎么写**以 `references/semantic-policy.md` 为门槛：立场绑定 anchor；动机/利益默认不持久化，
只有直接自述或至少两条独立观察才可标【推断】。细则收在 `references/digest-analysis.md`。

### 4.6 思路与视角:第一方观点(书写准则见 references)

§4.2 已定义 `project` / `area` 的「思路与视角」章节:承载使用者 / 主管 / 同事**直接陈述**的
第一方观点(stated)—— 与 §4.5 的「推断」互补。带日期、带作者、只追加。

**怎么写**(【主张】/【意图】标记、作者标注、绝不硬化为事实、思路老化更快的处理)是 digest
行为准则,见 `references/digest-analysis.md`。与 `context.md`(§10)的分工:本章节挂在具体
`project`/`area` 上,`context.md` 是跨主题的工作底色。

---

## 5. journal/ — 时间线日志

`journal/<YYYY-MM>/<YYYY-MM-DD>.md`,按天追加,一事件一行:

```markdown
# 2026-05-20

- 14:30 摄取 [feishu_minutes] "Q2 路线图评审会" → 新建 event-2026-05-20-q2-review
  | 衍生 decision-q2-scope(新) | 更新 project-q2-roadmap、person-zhang-san
  | raw-2026-05-20-q2-roadmap-review
- 15:10 更新 decision-auth-approach ← raw-2026-05-20-auth-doc
  | 冲突:旧"方案A" vs 新"方案B" → 用户裁决"方案B",旧决策标 superseded
- 16:00 看板:记今日进展「与 X 对齐了下阶段排期」
```

每行含:时刻、动作、输入源、触达节点 id、raw_id、是否冲突。这是审计日志。

---

## 6. INDEX.md — 主索引

skill 自动维护,可从全部节点的 frontmatter + body 首行 TL;DR、加 `raw_data/` frontmatter 全量重建。
按 8 类分节,一行一节点:

```markdown
# 知识库索引

## 人员 (person)
| id | 标题 | feishu_id | department_path | TL;DR | status | last_verified |
|----|------|-----------|-----------------|-------|--------|----------------|

## 项目 (project)
| id | 标题 | TL;DR | status | last_verified |

## 主题领域 (area) / 组织 (org) / 事件 (event) / 决策 (decision) / 读物 (reading) / 思考 (thinking)
| …同上… |

## 定期摄取清单 (routine digest — 会定期更新、需周期性复查的源)
| 源 | 类型 | cadence | 上次摄取 | 关联节点 |

## 群聊摄取进度 (feishu_chat 增量高水位)
| 群名 | chat_id | 已摄取至 | 最近 raw_id |
```

- **「定期摄取清单」表** = 会定期更新、需周期性复查的源(滚动周会文档、群聊、Meego / Base / 风神
  保存视图等)。结构化来源由 `sources/*.json` 中启用的 `routine` 派生；没有 profile 的旧来源
  才兼容扫描带 `routine` 的 raw。两者都按稳定 `source_uid` 合并成一源一行，profile 的启用/
  禁用与 cadence 优先于任何历史 raw。`上次摄取` = 该源最近 raw 的规范化周期 / 窗口;没有周期的完整
  视图快照使用 `ingested` 日期。日期周期用 `YYYY-MM-DD`,ISO 周用 `YYYY-Www`,群聊窗口用完整
  高水位时间戳。「定期摄取」例程逐源 re-digest(见 SKILL「定期摄取」)。*替代了旧的「待消化」
  表 —— 后者无机制主动入列、形同虚设;`digest_status: pending/failed` 的中断 raw 改由扫
  `raw_data/` 兜底发现。*
- **「群聊摄取进度」表** = 每个摄取过的群一行,记 `chat_id` 与「已摄取至」(该群最近一次 `source_window` 的结束点 = 增量高水位,格式固定为完整 ISO8601,如 `2026-05-25T00:07:30+08:00`)。digest 群聊前查此表判断首次 / 增量,摄取后更新对应行;从 `raw_data/` 的 `feishu_chat` raw frontmatter 派生、可重建。**这是 agent「这个群摄过没、摄到哪」的唯一可见入口。**
- **`TL;DR` 列** = 节点 body 首行的一句话摘要(§4.2)。让查询时的语义匹配作用在
  「标题 + 摘要」而非仅标题上,大幅提升语义召回 —— 这是 byteworker 不引入向量库
  也能做语义检索的关键:检索器是当前 agent/模型本身,只需把语义面在 INDEX 里铺够。
  摘要过长则截断到一行。
- **人员表的 `feishu_id` / `department_path` 列** —— 前者支持按飞书邮箱英文 id 直接检索到
  对应的人，后者支持按当前通讯录部门路由人员。二者都从 person frontmatter 确定性重建；
  历史节点没有 `department_path` 时显示 `?`，不得从 TL;DR 或正文猜填。
- 查询先运行无状态 `bin/kb-query.py search`，得到字面/全文候选、覆盖回执和预算内一跳 links；
  Agent 再按语义补召回并定向读取。节点有 `[E]` 时用 `kb-query.py evidence` 解析精确 sidecar。
- 一致性兜底:某类 `knowledge/<类型>/` 文件数 ≠ INDEX 该节行数 → 触发全量重建。
  (纯内容编辑不改行数,无法靠计数发现 → 故增量更新是主路径。)
- 单类节点行数 > 200 → skill 必须提示该类按子目录分片(TODOS)。

---

## 7. templates/ — 节点骨架

```
templates/
  README.md            模板使用说明
  digest-plan-v1.json  单来源 digest 临时 manifest 结构参考(填业务内容后只能放系统临时目录)
  digest-plan-v2.json  新单来源 plan；只引用 source bundle，不复制来源/锚点
  digest-batch-plan-v2.json  新多来源原子 plan；每个 input 只引用 source bundle
  digest-batch-plan-v1.json  旧多来源原子 digest 兼容模板
  source-bundle-v2.json  来源适配器输出契约结构参考
  node-person.md       \
  node-project.md       \
  node-area.md           \  各 = §4.1 通用 frontmatter
  node-org.md            >  + §4.2 对应 type 的 body 章节
  node-event.md         /   + 章节内 <!-- 指引 --> 注释(填什么、从哪提取)
  node-decision.md     /
  node-reading.md
  node-thinking.md       thinking 的低结构自然语言骨架
  context.md             context.md 文件骨架(全局上下文,§10;首次使用整份复制为初始 context.md)
  todo.md                todo.md 文件骨架(用户行动状态,§11;首次使用 Todo 时整份复制)
  report-daily.md        日报骨架(daily 输出到 reports/daily/)
  report-weekly.md       周报骨架(weekly 输出到 reports/weekly/)
  report-morning.md      Dreaming 晨报骨架(输出到 reports/morning/)
```
无法判定 type 时不得写入；按 `references/semantic-policy.md` 给出最多 3 个候选类型、reason
code 与证据，请用户确认。

---

## 8. 已锁定的决策

1. **领域分类** — 不预设 topic 清单;area/org 节点按需生长(实体图模型)。
2. **会议待办不接飞书任务** — `event` 的"待办事项"仅以 md 形式存在节点内;
   skill **不调用 lark-task 创建真实任务**。
3. **raw_data 永久保留** — v1 原始输入文件永久保留,不自动删除/归档;
   归档策略见 TODOS.md(P2,规模触发后再做)。
4. **逻辑与数据严格分离** — skill 仓库只含 agent 逻辑(可进 git/GitHub);所有业务数据
   (`knowledge/`、`raw_data/`、`provenance/`、`journal/`、`INDEX.md`)存在用户指定的独立目录(默认名
   `byteworker_kb`),**绝不进 skill 仓库的 git**。数据目录路径记于 `.kbconfig`(gitignore)。
   数据目录有自己的**独立本地 git**(回滚用,永不 push),首次使用时由 skill 询问并初始化。
5. **新增并扩展 `reading` 节点类型** — 外部读物(blog/论文/wiki)与内部路线思考 / 方法论 /
   调研 / 技术白皮书的资料卡,与工作知识同图、独立成类(`knowledge/readings/`);
   外部来源新增 `source_type: web`,内部资料仍使用 `source_type: feishu_doc`。见 §0、§3、§4.2。
6. **真相源/派生不变量 + auto-link + 重建一等化** — 显式锁定数据不变量(§1.C);写节点时
   自动从 body 提及的节点 id 连边(auto-link);「重建 INDEX」提为一等操作并补灾难恢复。
   源:gbrain 架构借鉴(reading-gbrain-system-of-record / reading-gbrain-retrieval)。
7. **检索栈:INDEX 路由 + grep 全文 + agent 语义** — INDEX 增 `TL;DR` 列扩大语义面(§6);
   `search` 双路召回(扫 INDEX 做语义召回 + `grep` 做全文召回)再图遍历;**不引入向量库/DB**
   —— 个人库尺度下检索器即当前 agent/模型本身。源:gbrain 混合检索借鉴(reading-gbrain-retrieval)。
8. **群聊增量摄取** — 群聊是持续消息流,同一群反复摄取;`feishu_chat` raw 的 `source_window`
   结束点 = 高水位,在 INDEX「群聊摄取进度」表登记(agent 据此查首次/增量),
   `bin/pull-chat.sh --since-last` 据此自动续拉下一窗口。每窗口一个 event,实体节点跨窗口
   累积更新。见 §3、§4.3、§6、SKILL「群聊摄取补充」。
9. **定期摄取(routine digest)** — 会定期更新的源(滚动周会文档、群聊、Meego / Base / 风神保存视图)
   经用户确认后，结构化来源写各自 `sources/` profile 的 routine；无 profile 的旧来源兼容
   raw `routine` 标记。INDEX「定期摄取清单」由 profile 优先派生。「定期摄取」例程
   逐源增量 re-digest,支持手动触发与 skill-use 到期提醒。见 §3、§6、SKILL「定期摄取」。
10. **第一方观点:思路与视角章节 + 全局 context.md** — 使用者/主管/同事的主观工作思路、想法、
   意图作为第一方输入纳入考量。挂在具体 project/area 上的观点 → 节点新增「思路与视角」章节
   (带日期、带作者、只追加日志,标【主张】/【意图】,§4.6);跨主题的工作底色 → 数据目录顶层
   新增 `context.md`(使用者手维护、按 intent 加载相关章节为「透镜」,§10)。出处严标、绝不硬化为事实。
11. **person 飞书 id + digest 重点关注** — `person` 新增 frontmatter 字段 `feishu_id`(飞书英文
   id = 企业邮箱前缀,全局唯一),作 person 实体消解主键、消歧同名;同名不同 `feishu_id` =
   不同人,须经用户确认(§2、§4.1、§4.3)。digest 时:结合 `context.md` 重点关注使用者本人 /
   其项目 / 团队 / 关注的人及其指令;命中重大事故 / 指标剧变等需高亮的内容,显著记录进节点
   并在 digest 后主动提醒用户(详见 SKILL「digest」)。
12. **person id 与 `feishu_id` 解耦** — person 节点 id = 稳定 slug(同其它 6 类,姓名关键词
   kebab-case),**一经生成永不改名**;`feishu_id` 仅作 frontmatter 字段(实体消解主键、消歧
   同名)+ INDEX 人员表一列。撤销曾短暂采用的「id ≡ feishu_id」方案 —— 后者在 `feishu_id`
   初始不可知、或永久为 `?` 时,被迫走「临时拼音 slug → 改名级联」,易漏错;解耦后全库再无
   任何 node 需要改名。见 §2、§4.1、§6。
13. **定期摄取到期判断改用状态文件** — 「到期提醒」不再扫 journal 散文找上次运行日期,
   改读数据目录的 `.last-routine-digest`(§1.B)。定期摄取例程每次运行后写当天日期 ——
   **空手而归也写**(「复查过」≠「有新增」);journal 行降为纯审计。见 §1.B、SKILL.md。
14. **报告归档快照** — 当前 writer 只生成 `reports/daily/`、`reports/weekly/` 与 Dreaming
   `reports/morning/`。报告不进入 INDEX,但每条事实必须回溯到原始 evidence；同周期再次生成
   可覆盖,但保留用户手动备注。I7 已移除 Inbox writer；历史 `reports/im/` 保持只读且不参与
   新知识库初始化。见 §12、SKILL.md。
15. **新增 `thinking` 节点类型** — 用户自己的认知、直觉、假设、推演与方案草稿以自然语言
   持续更新在 `knowledge/thinkings/`；只强制最小 frontmatter，状态仅为
   `effective|inactive`。它不要求 raw、固定章节或 TL;DR，不替代 reading/decision/context。
16. **digest 幂等与 raw 不覆盖** — raw frontmatter 增加 `source_uid` / `source_revision` /
   `content_hash` / `digest_key` 等运维字段;重复摄取同一来源同一正文必须 no-op,同源新版本写
   新 raw 并更新已有主记录节点;任何情况下都不得覆盖旧 raw 正文。见 §2、§3、references/digest-core.md。
17. **本地 Todo + 自然语言优先** — 数据目录顶层 `todo.md` 是用户确认后行动状态的唯一真相源;
   event / report 的待办只保留来源事实,不承担完成状态。digest 只产候选、必须经用户确认后入 Todo;
   用户日常以“明天提醒我”“刚才那个做完了”等自然语言操作,id 仅供内部关联。每次 skill 运行
   拉取式检查到期 / 临期项;无对话时不承诺后台推送,也不调用 `lark-task`。见 §11、references/todo.md。
18. **飞书文档评论进入证据链** — `feishu_doc` 正文与评论独立拉取、独立 hash;raw 保留全部评论
   (含已解决)、完整回复链、作者 / 时间 / 解决状态和正文锚点。评论变化即使不改变正文 revision
   也可触发同源新版本。直属上司与用户点名特别关注人员只提高抽取 / 提醒优先级,其观点仍按
   【主张】/【意图】/【观察】呈现,不自动升级为客观事实。见 §3、
   `references/digest-comments.md`。
19. **digest 确定性事务 + payload components** — Agent继续负责语义理解、依赖范围、冲突、
   实体消解与候选正文;`bin/digest-txn.py` 固化逐组件 hash、兼容幂等、候选 schema、
   `base_sha256` 并发保护、原子写入/回滚、INDEX/journal 与精确本地 commit。新 raw 用
   `byteworker-payload-v1` 描述正文、评论、白板等实际 payload;旧 raw 只读兼容、不强制迁移。
   飞书正文内嵌白板默认随当前来源只读取结构 JSON，不抓取或分析预览图片。见 §3、
   `references/digest-transaction.md`、`references/digest-whiteboard.md`。
20. **主要来源 + 节点事实证据** — raw 正文继续不可变;精确 block/comment/message 等 locator
   写入 `provenance/<raw_id>.json` sidecar。主记录带 `primary_source` /
   `primary_source_url`;节点关键事实用持久 `[E<n>]` 映射到 anchor,查询回答再生成动态
   `[S<n>]`。历史库通过默认不执行的 audit/plan/validate/apply 流程保守回填,不自动猜测
   多来源节点。见 §3.2、§4、`references/provenance.md`。
21. **轻量批量事务 + 确定性查询入口** — 新的多来源原子 digest 使用
   `digest-batch-plan/v2`，每个 input 只引用 SourceBundle；v1 只兼容历史调用。事务仍以
   乐观基线、短时文件锁和单次本地 commit 实现,不引入数据库。
   `bin/kb-query.py` 每次运行直接扫描节点,统一输出召回覆盖、一跳图扩展和 evidence 解析；
   不保存索引、不承担语义判断。见 §3、§6、`references/digest-transaction.md`。
22. **结构化视图采用快照 + 差异 + 晋升门槛** — Meego / Base / 风神每次保存完整规范快照并为
   记录/报表建 exact anchor；`source diff` 只产可重算的
   `baseline / added / changed / left_view`，
   其中 `left_view` 不代表删除。普通记录不进入实体图，只有满足
   `references/semantic-policy.md` 中 explicit decision、dated status change、time-bounded
   event、cross-record theme 或 long-running project 的最低证据才晋升；同一视图始终更新一张
   `reading` 主记录。见 §3.1、
   `references/digest-meego.md`、`references/digest-base.md`、`references/digest-aeolus.md`。
23. **结构化 raw 记录检索** — Meego / Base / 风神的普通记录/报表由
   `kb-query source-record` 从每个 `source_uid` 的最新完整快照按稳定 ID 或模糊标题有限召回；
   Agent 不直接扫描大 raw。历史查询
   必须显式开启并返回最新性标记；检索结果携带 raw 与 exact anchor 溯源。见 §3.1、
   `references/commands.md`、`references/machine-protocol.md`。
24. **异构来源通过 Bundle/Adapter 解耦** — 新单来源 digest 使用
   `byteworker-source-bundle/v2` + `digest-plan/v2`；来源 adapter 保留 provider 自身
   transport 和 payload，只在 bundle 边界统一身份、component、coverage、anchor 和可选
   record index。plan 不复制来源或 anchors；事务核心不新增 provider 分支。
   `digest-plan/v1`、Aeolus profile v1 和既有 raw/query 解析作为迁移期兼容层保留，达到
   `ARCHITECTURE.md` §8.3 的删除条件后再移除。
25. **person 通讯录画像随身份解析补全** — `bin/resolve-users.sh --format json` 除姓名 /
   `feishu_id` 外返回企业邮箱、当前部门路径和核验时间；新建 person 必须记录
   `directory_verified_at`，可见时同步 `enterprise_email` / `department_path`。部门是可变的
   当前目录属性：变化保留历史，空结果不清除旧值；仅明确命中已有 org 时连边。默认三列 TSV
   暂作旧调用兼容。见 §4.1、§4.2、§6 与 `references/digest-core.md`。
26. **全部 durable writer 共用一个 KB 写锁** — digest、Profile、provenance backfill、
   postflight 和非 digest mutation 都竞争 `.git/byteworker-write.lock`，锁内重验 staged/dirty/
   baseline；不同业务入口不得创建互不相见的私有锁。
27. **非 digest 写入使用 MutationPlan** — update/context/dashboard/report 不再让 Agent
   手工修改目标、INDEX、journal 或 Git；统一使用 §1.G 的 `byteworker-kb-mutation/v1`。
   thinking 复用 `update` operation 写 `knowledge/thinkings/`。
28. **冲突与语义阈值单一所有者** — 独立来源冲突默认并列并交用户裁决；只有 revision、
   supersede 或用户确认可改当前值。知识晋升、参与方推断和 IM 评分使用固定 reason/evidence/
   threshold，不以模型自由描述替代。
29. **Agent progressive disclosure 可测试** — `byteworker-workflow-routes/v1` 声明独立入口的
   required/on_error/source/features 闭包并设置字符预算；子 Agent、自动报告和 Wiki resume
   不依赖隐式“普通流程”。

**schema 以本文件为准;后续扩展在此节登记。**

---

## 9. dashboard.md — 工作看板

数据目录顶层文件,与 `INDEX.md` 并列。一个**实时工作视图**,**不是知识节点** —— 回答
"我现在该看什么"。

- **持久存储**(用户状态,只此一处保存):📌 长期关注列表、⚠️ 手动提醒。
- **渲染**(每次刷新重算,不持久依赖):📌 各关注项的当前状态、⚠️ 派生项、📅 今日进展。

结构:

```markdown
# 工作看板 · dashboard
> 最后刷新:<YYYY-MM-DD HH:MM>

## 📌 长期关注
| 关注项 | 绑定节点 | 关注什么 | 当前状态 |
|--------|----------|----------|----------|

## ⚠️ 需要关注
- (派生)<陈旧节点 / 未裁决冲突 …>
- (手动)<用户提醒>

## 📅 今日进展(<YYYY-MM-DD>)
- <当天 journal 渲染>
```

- **📌 关注项**:`绑定节点` 列填知识节点 id(能绑则绑),或留空(自由文本项)。
  `当前状态` 列刷新时从绑定节点拉 TL;DR/状态;自由文本项写"—"。
- **📅 今日进展**:**不独立存储**,刷新时从当天 `journal/` 渲染;用户报告的进展先写入
  journal、再渲染到此。跨天自动重置(journal 即历史归档)。
- **⚠️**:派生项刷新时由轻量新鲜度/冲突扫描得到;手动提醒持久存在文件内。
- 看板是 view —— 每次"看板"触发都重新渲染,**不会过时**。

---

## 10. context.md — 全局工作上下文

数据目录顶层文件,与 `INDEX.md` / `dashboard.md` / `todo.md` 并列。**使用者通过对话维护**的
格式化全局工作上下文 —— 语义任务按 intent 加载相关章节投影，作为 digest / search / brief /
dashboard / todo 的「透镜」。

- **性质**:真相源、不可派生。skill 在 digest / search 等流程中**只读、绝不自动改写**;
  用户明确要求时由 agent 代为增删改(SKILL 的 `context` 子命令)—— 完全通过对话式 agent
   (Codex、OpenClaw 等)使用本 skill 的用户无法直接编辑文件,**必须靠 agent 代维护**；实际副作用
   统一由 KB mutation 执行。
- **保持简短**:它是「透镜」不是「档案」。日常通过 `byteworker-context-view/v1` 读取相关章节；
  12k 字符给 warning、24k fail closed，完整文件 mutation 上限 32 KiB，不静默截断。
- **用法**:身份表用于本人识别;职责 / 重点用于 digest 相关性与 Todo 候选判断;时区 / 默认时间
  用于自然语言提醒解析;search / brief 在客观答案旁带出使用者视角,并在事实与陈述意图冲突时提示。
- **陈述性质分开**:“我的身份 / 我的职责范围”是用户提供的信息;“我的当前重点 / 主管方向”
  等主观内容呈现时标为“你的视角 / 用户陈述”,不把意图硬化为客观事实。
- **与「思路与视角」章节的分工**:`context.md` 是**跨主题**的工作底色;节点的「思路与视角」
  章节(§4.6)是**挂在具体 project/area 上**的观点。

**结构由模板锁定** —— 骨架见 skill 目录的 [`templates/context.md`](templates/context.md):固定七个
章节 `我的身份` / `我的职责范围` / `我的当前重点` / `主管方向` / `当前约束` /
`交互与提醒偏好` / `背景信息`。身份使用固定表格(姓名、别名、`feishu_id`、person 节点、时区);
其它章节使用简短条目,变更型信息优先带日期(`- <YYYY-MM-DD> —— <一句话>`)。
首次使用、或数据目录缺 `context.md` 时,由 skill **整份复制**该模板初始化 —— 统一模板,避免各用户
写出五花八门的格式。各章节无内容则留空;`<!-- 指引 -->` 注释保留(持续引导用户、不渲染)。
用户明确维护时使用 mutation 的 `replace_section`，由 validator 保证章节名、baseline、journal、
精确 commit 和失败回滚；Agent 不直接覆盖文件。

## 11. todo.md — 用户行动与提醒

数据目录顶层文件。它不是知识节点、不进入 `INDEX.md`,是用户确认后行动状态的唯一真相源。
event / report 中的“待办”记录来源当时说了什么;`todo.md` 记录用户后来是否确认、完成、延期或取消。

固定结构：

```markdown
# TODO
## Active
### [ ] T-20260723-001 · 提交周报
- kind: task
- status: open
- created_at: 2026-07-23T10:30:00+08:00
- updated_at: 2026-07-23T10:30:00+08:00
- due_at: 2026-07-24T18:00:00+08:00
- remind_at: 2026-07-24T09:00:00+08:00
- time_expression: 明天
- snoozed_until:
- source: direct:user
- links: project-example
- reason:
- last_reminded_at:
- note:

## Completed
```

- **内部 id**:`T-<YYYYMMDD>-<三位序号>`,创建后不变。只供去重、关联、脚本更新;用户侧不要求输入。
- **kind**:`task` / `follow_up` / `watch`;**status**:`open` / `waiting` / `done` / `cancelled`。
- **时间**:`due_at` = 截止,`remind_at` = 何时提醒,`snoozed_until` = 暂停提醒到何时;
  均用带时区 ISO8601。`time_expression` 保留用户原相对时间短语,回显时同时给出绝对时间。
- **来源**:直接输入写 `direct:user`;digest 确认项写 event / raw / report id 或 URL。`links` 可连
  project / person 等知识节点,但 Todo 不加入节点双向 links,避免把操作状态混进知识图谱。
- **写入**:`bin/todo.py` 负责解析受支持的相对时间、校验状态，并在共享 KB 写锁内原子重写
  `todo.md`、追加 journal、精确本地 commit 和失败回滚；
  agent 负责从自然语言提取标题、区分截止 / 提醒语义、在多个相似项间做语义消解。
- **提醒**:每次 skill 运行检查到点提醒、逾期、24 小时内临期(窗口可由 context 配置);无命中静默。
  `last_reminded_at` 用于限频。它是拉取式能力,不代表后台 scheduler。
- **确认闸门**:用户直接说“记个待办 / 提醒我”即授权写入;digest 自动分析只产候选,用户明确选择后才写。
- **完成历史**:done / cancelled 移到 `Completed`,仍留在同一个文件中供追溯。

完整交互与时间规则见 `references/todo.md`;骨架见 `templates/todo.md`。

## 12. reports/ — 归档报告快照

报告文件不是知识节点,不进入 `INDEX.md`,但属于用户可手改的真相源快照。当前 writer 用它们
归档某天、某周或晨间窗口的工作总结，回答“这段时间发生了什么重要事，与我和团队有什么关系，
后续该看什么”。

目录与命名:

```text
reports/
  daily/
    2026-05-25.md
  weekly/
    2026-W22.md
  morning/
    2026-06-02.md
```

- **生成来源**:
  - 自动日报 / 周报或自然语言补跑:先完整运行全部已登记且启用来源的 routine digest，再召回
    范围内 `journal/`、`raw_data/` frontmatter、`knowledge/` 节点及其 links。自动运行不受
    `.last-routine-digest` 七天交互提醒阈值限制。
  - Dreaming morning/daily/weekly：只消费 committed Finding projection、coverage 与 durable KB
    查询指针，不读取 spool；必须通过 include_report Action claim 提交。
- **模板**:skill 目录 `templates/report-daily.md`、`templates/report-weekly.md`、
  `templates/report-morning.md`。
- **覆盖规则**:同一日期 / 周 / 晨间窗口再次生成可覆盖报告正文；用 mutation 的
  `replace_preserving_sections` 确定性保留旧 `## 手动补充 / 备注`。
- **排序**:章节内带时间条目按事件发生时间倒序;时间不明放末尾并标注。
- **溯源**:每个事实性条目在正文用 `[S<n>]` 绑定引用;「引用」章节(旧报告为「来源索引」)
  继续沿节点 / 报告 / journal
  追到原始 raw,列具体文档 / 妙记录屏 / 会议 / 群聊窗口、原文时间或覆盖范围、`ingested`
  收录时间、版本与 raw_id。节点 id、raw_id、journal 日期或报告路径不能单独充当原始出处;
  无来源不写事实结论。Dreaming Finding 必须沿 evidence locator 回到消息窗口；需要长期沉淀时
  重新采集并走标准 SourceBundle/DigestTxn。
- **历史兼容**:I7 前生成的 `reports/im/` 可继续存在，doctor 可只读校验其文件名与引用结构；
  当前 skill 不创建、改写、迁移或删除该目录及内容。
- **git**:报告、journal 和本地 commit 由 KB mutation 同成同败，永不 push；Agent 不手工暂存。
