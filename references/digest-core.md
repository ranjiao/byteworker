# byteworker · digest 主流程

普通单来源使用本文件、`digest-flow.md`、`digest-inline.md` 与路由返回的来源和 policy 文件。
完整日志、并发、事务兼容手册按 feature 或失败加载，不沿解释性链接整包展开。

## 流程与闸门

1. **分类与范围**：识别当前指定的 source type；通过 `workflow-budget resolve --workflow digest`
   加 `--source-type` 获取闭包。不带来源加 `routine`；Wiki 空间先走 wiki_space，单页仍是
   feishu_doc；同场会的日历、妙记、投屏文档按会议簇一次确认。大型输入先预估节点和依赖规模，
   合并确认摄取深度，再加 large_input；不得无差别铺开。
2. **抓取**：先 `digest-flow start`，再按来源细则抓取全部已确认 component，保存原系统 locator。
   飞书正文和全部评论双路抓取，内嵌白板只取结构 JSON；顺序分页串行，coverage 不完整不得假装
   complete。外部引用不是自动授权，依赖正文须先通过下面的依赖闸门。
3. **准备与幂等**：`digest-flow prepare` 自动构造 SourceBundle v2、运行 transaction preflight、
   analysis prepare 和 dependency/semantic planner。Agent 不手算 hash、digest_key，不手拼 raw。
   noop 按回执结束；new_source/new_version 按 next_action 继续。resume_failed 表示已有相同 payload
   的 pending/failed raw，必须加 recovery 读事务细则、核查历史 raw 后人工恢复；不得自动覆盖或继续
   生成候选/提交。正文未变不能证明评论或白板未变。
4. **重要依赖**：只消费 packet 的 dependency_candidates，按 `digest-dependencies.md` 判断重要性，
   复用足够新的已摄取版本。没有重要依赖静默继续；有则在读取依赖正文和写节点前合并询问范围。
   拒绝或暂缓后继续当前对象，标注“依赖未摄取 / 待核实”，不补写缺失事实，不自动递归二跳。
5. **语义分析**：只消费一次 packet 的 sections、参与者和 anchor 索引，按 `semantic-policy.md`
   区分【事实】/【主张】/【意图】/【观察】/【推断】。正文、评论、白板不重复扫描；补证只按
   component/path 或 anchor 定点回读。读取已产出的计划，不重复运行
   `digest-parallel plan --stage semantic`；只有 planner 选择 parallel 才加 parallel_execution。
6. **冲突**：生成最多 32 条短 query，一次 `kb-query conflict-search` 召回同源节点和有限 snippet，
   再按 `digest-inline.md` 规划 conflict 阶段。仅 snippet 不足以裁决具体事实时定点读节点。
   `conflict-policy.md` 是唯一动作表；独立来源冲突须等待用户裁决，来源较新本身不能覆盖。
7. **候选**：先过 `write-rules.md`「信息增量门槛」，再按下节扇出；使用 `templates/node-<type>.md` 与 `templates/digest-plan-v2.json`。
   更新节点先合并原内容，保存基线 `base_sha256`，新增/删除 link 的反向节点同时纳入 plan。
   原 raw 永不覆盖；raw_id/path 冲突加版本或 hash 后缀。plan 只引用 Bundle，不复制 source/anchors；
   evidence 和主来源按 `provenance.md`。候选、plan、artifact 只放系统临时目录或 KB 私有目录。
8. **提交与汇报**：只调用 `digest-flow commit`，内含完整校验与锁内复验；独立 validate 只用于排障。
   以 `status=committed`、commit、raw_id、节点和 warning 回执为准；noop 不重复写，失败不声称完成。
   不得仅凭 Agent已生成候选就声称落库。多来源共同更新同一节点或须同成同败时加 batch，使用
   `digest-batch-plan/v2`，不得拆成部分提交。结束只核验紧凑回执，不重读完整 raw/候选/diff。

## 扇出与语义规则

- 有可入库内容时必产一个主记录：会议、群聊窗口用 event；外部读物和内部路线思考/方法论/调研/白皮书用
  reading；结构化视图也只产一张同源 reading。会议簇不按物件拆主记录。
  无增量按 write-rules 维护来源元数据；无有效内容的群聊按 digest-chat 跳过。
- 同一 source_uid + digest_period/source_window 更新已有主记录，不因新版本重复建节点；决策按
  同一事实去重，结论改变走冲突或 supersede。结构化普通记录留 raw/provenance，晋升只按
  `semantic-policy.md` 的 reason 和最低证据；left_view 不表示删除。
- 外部读物默认只产 reading；内部资料按证据抽取已生效 decision、更新相关实体，不把整篇塞进
  project/event。内部资料加 internal_reading；立场或“思路与视角”加 stakeholder_analysis。
- 每个明确生效决策产一个 decision；涉及的 person/project/org/area 先比对 INDEX 和同源召回，
  命中则更新，不建重复实体；歧义须确认，语义相似不等于同一事实。
- **库内标题消歧**统一按 `write-rules.md`：不得为了消歧改写 raw 正文或 `source_title`。
  无法证实归属时标“归属待确认”。event「事件信息」或 reading「来源」保留原始可打开链接；
  会议关联文档和日历找到则记录，未找到不编造。
- 人员批量用 `resolve-users.sh --format json --jobs 4`，按 feishu_id 消解；新建须有真实 id，失败
  保留姓名/open_id 并报告待解析。resolved_at 写 directory_verified_at，可见邮箱/部门同步到
  frontmatter 和基本信息；部门变化保留带日期历史，查询为空不清除旧值。同名不同 id 不合并，
  向用户确认后分别建节点；department_path 只有明确命中已有 org 才连边，不按路径片段自动建组织。
  project/org/area 有歧义也须确认。
- area/org/person 的范围和关系按 `write-rules.md`：用实时完整 `department_path` 作为正式组织名，
  负责人确认日期与目录核验日期分开记录。不得从职位、协作或同现推断成员、负责人或汇报关系。
- 使用 context view 的 digest 投影关注本人、项目/团队、直属上司和用户点名人员；职位只提高
  关注度，不提高证据置信度。主观意图不得变成客观事实；重点事故、指标变化、重要指令必须进入
  相关节点并在汇报单独提醒。跨主题工作底色不写实体或 context。
- 会议中的原始行动项保存在 event，负责人和截止时间按来源记录。
- 汇报说明周期范围、扩展或未摄取依赖及其影响。长流程只在真实阶段变化时给短状态；单阶段超过
  60 秒可一次 heartbeat，不贴原文、不主动轮询，不做主/子重复语义分析。
