# Token 与处理时长优化计划

本文跟踪 Byteworker Agent 工作流的 token、模型调用次数和墙钟时间优化。优化不得削弱完整来源
覆盖、provenance、冲突闸门、事务复验、私有数据边界或 `status=committed` 成功判定。

## 基线

- 2026-08-28 审计的 3 个无用户插话成功 digest 分别触发 25、55、67 次模型调用，总处理量约
  406 万、556 万、722 万 token，其中 92%--99% 是缓存输入。
- 6 个 committed digest 的中位耗时为 6.45 分钟，范围为 1.26--14.86 分钟。
- 当前 `digest-run` 只记录阶段耗时和业务无关计数，不能归因模型调用或 token。
- 当前 workflow budget 按基础 reference 闭包的字符数检查，不含 `SKILL.md`、source type、feature、
  worker prompt、context projection 或实际来源内容。

## 执行顺序

### T1. Token ledger 与模型调用观测

状态：`completed`

范围：为 `digest-run` 增加可选、宿主无关的 usage 事件，记录 stage、worker role、模型调用次数、
input/cached/output/reasoning token；没有宿主 usage 时允许显式标记 estimated，但禁止伪装为实测值。

验收：

- 日志只包含固定枚举和非负整数，不保存模型名、正文、标题、URL、prompt、argv 或自由文本。
- `list/show` 返回 run 总 usage 和逐 stage usage，并区分 measured/estimated。
- 重复 usage event 通过稳定 `call_id` 去重或拒绝，worker usage 能归入同一 run。
- CLI、单元测试、架构文档和行为 reference 同步。

完成记录：2026-08-28 新增 `digest-run usage`，支持 measured/estimated、固定 worker role、原始
call id 哈希幂等、终态后补报、run/逐 stage 聚合。验证：54 个受影响测试通过，`compileall`、
`git diff --check` 和 launcher shell 语法通过。

### T2. 确定性 digest orchestrator

状态：`completed`

范围：用单一公开 facade 驱动无语义的 start/stage/capture/bundle/preflight/prepare/transaction 编排，
只在依赖授权、语义分析和独立冲突等真实 Agent 决策点返回紧凑 next action。

验收：

- 小中型 digest 的标准路径不再要求 Agent 手工成对调用阶段日志或拼装重复命令。
- orchestrator 可恢复、幂等、fail closed，不持有语义裁决权，不改变事务成功边界。
- 合成流程中确定性工具轮次显著少于当前基线，并有契约测试。

完成记录：2026-08-28 新增 `digest-flow start/capture/prepare/commit/status`，把 classify、抓取、Bundle、
preflight、analysis packet 和 transaction 的成对日志收敛为单一可恢复 facade；156 个相关测试通过。

### T3. 精简常驻 SKILL.md

状态：`completed`

范围：`SKILL.md` 只保留意图路由、全局安全边界和成功判定；Dreaming、组织治理、报告和来源细则
移动到条件 reference，避免无关 workflow 常驻加载。

验收：

- `SKILL.md` 字符数相对当前基线至少下降 30%。
- 每个 workflow 的必要行为仍由机器可检查闭包覆盖；独立 worker 不缺安全规则。
- help、Dreaming、报告、digest 和 mutation 路由契约通过。

完成记录：常驻入口从基线 10,994 字符降至 7,581 字符，下降 31.04%；Dreaming 与 area/org/person
细则改为条件闭包，digest/update 都显式包含 `write-rules.md`。55 个相关契约通过；TRAE 细则的已知
文案漂移保留在 T6 修复，不是本次路由迁移造成。

### T4. 完整闭包与真实 token 预算

状态：`completed`

范围：预算覆盖 `SKILL.md + required + source_type + features + worker prompt + context projection`；
使用稳定 tokenizer 时记录真实 token，否则明确使用保守估算并保留方法版本。

验收：

- 测试不再把字符数命名为 token budget，也不忽略条件文件。
- manifest 分离静态规则、动态 context、来源 packet、总输入和输出预算。
- 超预算时给出确定性路由或压缩动作，不静默截断证据。

完成记录：新增 `workflow-budget inspect` 与 routes v2，覆盖 router、required、source type、features、
on_error、worker prompt 及显式 context/source packet；预算分离静态、动态、总输入和输出。固定
tokenizer 不可用时标记 `byteworker-conservative-token-estimate/v1`，所有 18 个 workflow 的完整条件
场景预算测试通过，超限测试返回固定 action。

### T5. Token-aware 并发计划

状态：`completed`

范围：planner 同时估算 inline/parallel 的 token、worker 启动成本和墙钟收益；只有收益满足固定策略且
总预算允许时才 fan-out，final reducer 使用专用紧凑闭包。

验收：

- plan receipt 包含 inline/parallel 估算、选择 reason code 和预算余量。
- 小型 dependency/conflict 默认批量单次判断，不因条目数阈值机械启动 worker。
- 并发仍最多 4 路，保持完整 coverage、input hash 和单 reducer 边界。

完成记录：planner 回执新增 inline/parallel token、wall time、选择 reason 和预算余量；固定最小项数、
token floor、30 秒/25% 收益及 worker/reducer/总预算全部通过才 fan-out。小 dependency/conflict 保持
inline，final reducer 使用不继承 digest 的专用紧凑闭包；30 个相关测试通过。

### T6. Stale run 与验证噪声

状态：`completed`

范围：区分 active、waiting_user、stale；停止对 stale run 无限累加活跃耗时，并修复已知 Dreaming
文案契约漂移及重复失败调查路径。

验收：

- 超过固定无 heartbeat 窗口的 run 在 list/show 中显式 stale，active duration 截止于最后事件。
- 用户等待状态可显式记录和恢复，不冒充失败或成功。
- 架构契约、受影响测试、全量测试、shell 语法和覆盖率门禁通过。

完成记录：新增 `wait/resume/heartbeat` 生命周期，6 小时无生命周期事件的活跃 run 显式标记
`stale`；等待区间和 stale 空档不计入活跃时长，usage 补报使用独立时间戳且不刷新生命周期。
Dreaming TRAE 宿主边界已按契约收敛。验证：162 个架构与受影响测试通过；全量 627 个测试通过；
分支感知覆盖率 77.4%（门禁 75%）；`compileall`、workflow routes JSON、launcher shell 语法及
`git diff --check` 均通过。

## 完成记录

每完成一项，在对应章节把状态改为 `completed`，记录验证命令和关键结果；若验收标准变化，必须
先在本文解释原因，不得只在实现中静默漂移。
