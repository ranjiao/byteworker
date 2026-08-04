# Dreaming：可选后台运行机制

Dreaming 是 Byteworker 的主动后台运行机制。它通过宿主本地定时任务唤醒，统一调度
`process/morning/daily/weekly/maintenance/recovery` job；不是常驻服务，也不是 digest 的新实现。

## 启用闸门

Dreaming 默认关闭。普通 preflight、digest、search、update、brief、dashboard、todo 和
旧自动报告都不读取 Dreaming 状态。

Dreaming local state 使用 `byteworker-dreaming/v2`。已有 v1 state 第一次被 Dreaming 读取时，
确定性状态层会先在 `state/dreaming/migrations/` 写 `0600` 本地备份，再原子替换为 v2；
`state/dreaming/` 为 `0700`。未知 schema 或迁移失败时 Dreaming fail closed，不要手改 JSON，
既有命令和旧自动报告继续独立工作。

用户明确要求启用时，先加载 `references/dreaming-onboarding.md`，完成其中的能力导览：
讲清 Dreaming 与 digest 的差异、全部 job、Finding/Action 生命周期、默认授权、隐私、成本、
机器条件、维护和退出方式。不能用下面的成本提示代替教程。导览后必须单独询问是否启用。

用户明确同意后，还必须原样说明：

> Dreaming 会周期性读取来源并调用模型，产生额外网络、模型和本地存储开销。若希望按设定时间
> 及时运行，本地机器必须保持开机、唤醒、联网，并允许宿主执行本地任务；休眠或关机期间不会
> 运行，只能在恢复后补跑。

用户确认后才可以：

1. 在宿主中创建 local 定时任务，工作目录必须是私有知识库目录。
2. 调用：

```bash
bin/byteworker dreaming enable \
  --kb "<知识库绝对路径>" \
  --harness "<当前宿主>" \
  --timezone "<context.md 时区>" \
  --acknowledge-capability-tour \
  --acknowledge-machine-runtime
```

3. 创建成功后立即运行 `dreaming status`，核对 `enabled=true`、timezone、harness 和 job。

禁止在安装、升级或 session preflight 中自动启用或询问。未完成导览并得到确认时不能代填
`--acknowledge-capability-tour`；未确认成本和机器条件时不能代填
`--acknowledge-machine-runtime`。

## 禁用与状态

```bash
bin/byteworker dreaming status --kb "<知识库绝对路径>"
bin/byteworker dreaming disable --kb "<知识库绝对路径>"
bin/byteworker dreaming retry-job --kb "<知识库绝对路径>" --job process
bin/byteworker dreaming retry-job --kb "<知识库绝对路径>" --job maintenance
```

- 缺失状态等同 `disabled`，且 `status` 不创建状态文件。
- 有活跃租约时禁用失败；等待完成或租约过期后重试。
- 禁用不删除 findings、receipt、报告，也不修改旧 `report-automation` 配置。
- Dreaming 状态损坏只影响 Dreaming；不要转而修改或阻塞既有命令。

## 宿主 tick

目标态由一个 local 宿主任务每 30 分钟运行 `templates/dreaming-runner.md`。runner 调用：

```bash
bin/byteworker dreaming run-due \
  --kb "<知识库绝对路径>" \
  --owner "<宿主任务 id>" \
  --lease-seconds 7200
```

返回值：

- `disabled`：安静退出。
- `idle`：没有到期 job，安静退出。
- `busy`：已有有效租约，安静退出。
- `leased`：只执行返回的 `job/period`，并保存 `lease.token`。

一轮最多领取并执行一个 job。不要在 Agent 内自行计算另一个 period，也不要循环直到清空。
候选按 deadline、ready age 和稳定 job name 选择；失败 job 遵守 `next_attempt_at`，不会持续压住
morning/recovery。授权等人工阻断修复后显式运行 `retry-job`。

长任务在租约到期前可续租：

```bash
bin/byteworker dreaming renew --kb "<知识库绝对路径>" \
  --token "<lease.token>" --lease-seconds 7200
```

## Job 执行

### `process`

IM grant 默认关闭：

```bash
bin/byteworker dreaming grant set-im --kb "<KB>" --mode monitored
bin/byteworker dreaming grant set-im --kb "<KB>" --mode all_visible \
  --acknowledge-all-visible
bin/byteworker dreaming grant set-im --kb "<KB>" --mode off
```

`all_visible` 会扫描当前用户可见的 P2P 和免打扰会话，未明确确认不得代填
`--acknowledge-all-visible`。`--persist-finding` 是独立授权；I3 之前即使开启也不会产生 Finding。

当前 I2 提供确定性采集：

```bash
bin/byteworker dreaming process prepare --kb "<KB>" --source im \
  --start "<ISO8601>" --end "<ISO8601>"
bin/byteworker dreaming process abort --kb "<KB>" \
  --batch-id "<EB-id>" --error-code "<stable-code>"
```

`prepare` 只生成私密 spool 与 `collected` EvidenceBatch，返回 batch id、相对 manifest path、数量和
coverage，不返回消息正文。monitored 读取已登记的 chat Profile；all_visible 的 queryless search
永远标 `best_effort`。预算截断形成时间切片 gap，page token 不跨 attempt 保存。

拿到 collected batch 后，读取 `references/dreaming-analysis.md` 生成 FindingBundle，再调用
`process commit`。持久化与恢复规则见 `references/dreaming-consolidation.md`。

I3 只生成/整合 Finding；`process` 不自动创建 Todo、订阅新来源、写报告或执行知识入库。

Finding 需要进入报告、Todo、来源确认或知识候选时，必须读取
`references/dreaming-actions.md`，通过 Action Policy + Ledger 执行。不得从 Finding 直接调用
KB mutation、Todo 或 DigestTxn。

用户显式单次处理、Finding review/explain/feedback 和 shadow 评估见
`references/dreaming-review.md`。这些前台能力不等于启用 Dreaming 后台。

### `morning`

按 `references/dreaming-reports.md` 读取 committed Finding、Todo 和已有 KB，生成
`reports/morning/<YYYY-MM-DD>.md`。事实必须遵守 citations；没有完整 coverage 时明确标 partial。

### `daily` / `weekly`

迁移完成前默认禁用。启用后按 `references/dreaming-reports.md` 消费 Dreaming checkpoint 和
Finding history，不能再次独立执行完整 routine digest。

### `recovery`

只补偿一个过期 lease、gap、失败 receipt 或 delivery outbox。没有待补偿项时成功 no-op。

### `maintenance`

工作日低频运行 KB doctor。必须加载 `references/dreaming-maintenance.md`：先只读 scan，再只通过
公开 doctor facade 执行 finding 明确声明的确定性低风险修复；复扫后筛选真正需要用户判断的
error、证据/身份风险和自动化阻断项。不得猜业务字段、创建 Profile 或扩大来源授权。

重要问题通过当前宿主给用户有限摘要，并以
`DOCTOR_USER_DECISION_REQUIRED` 进入 `waiting_for_user`，直到用户处理/忽略后显式
`retry-job maintenance`；这避免后台重复提醒。maintenance 失败只影响自身，不阻塞 process、
报告或 recovery。

## 完成回执

成功：

```bash
bin/byteworker dreaming complete \
  --kb "<知识库绝对路径>" \
  --token "<lease.token>" \
  --run-status success \
  --artifact-path "<可选 KB 相对路径>" \
  --coverage-checkpoint "<有限 checkpoint>"
```

部分或失败必须提供稳定错误码：

```bash
bin/byteworker dreaming complete \
  --kb "<知识库绝对路径>" \
  --token "<lease.token>" \
  --run-status failed \
  --error-code "SOURCE_AUTH_REQUIRED"
```

`partial/failed` 不覆盖 `last_success`。报告生成和投递是不同成功状态；没有投递回执时不能声称用户
已收到。

## 日报/周报迁移

启用 Dreaming 不接管现有日报/周报。只有用户明确要求统一迁移、旧宿主任务已停用后，才运行：

```bash
bin/byteworker dreaming manage-reports \
  --kb "<知识库绝对路径>" \
  --enabled true \
  --acknowledge-owner-released
```

如果 `state/report_automation.json` 仍显示日报或周报已配置启用，命令必须以
`DREAMING_REPORT_OWNER_CONFLICT` 拒绝。Dreaming 不修改该文件，也不替用户停旧任务。

## 解耦边界

- Dreaming 只通过 `bin/byteworker` 公开工具调用既有能力。
- 不修改 digest/search/update 等命令参数、返回值和成功判定。
- 既有命令不 import Dreaming，也不需要 Dreaming state。
- Dreaming state 位于 `state/dreaming/`，只写 `/state/` Git 排除目录。
- Dreaming 失败不能触发对既有知识、报告或自动报告状态的回滚。
