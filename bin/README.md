# byteworker 命令手册

这里列出抓取、校验、查询、事务写入、维护和更新命令。理解资料、判断冲突等业务语义由 Agent 处理。

## 1. 先选择正确的入口

### Agent 或自动化调用

交互会话开始时运行一次预检。

```bash
bin/byteworker preflight --interactive
```

检查通过时无输出。有输出时只处理 `byteworker-session-preflight/v1.notices`。已登记来源会自动识别。
即将访问未登记的飞书或 Meego 来源时，加 `--require feishu` 或 `--require meego`。
自动报告、Dreaming runner 和其他定时任务用 `preflight --unattended`。省略参数时也按无人值守处理，
两种写法都不返回功能建议。

后续命令走同一个启动入口，由它解析运行环境。

```bash
bin/byteworker <tool> [tool arguments...]
```

手动查看用分组帮助。Agent 和自动化可读机器清单。

```bash
bin/byteworker --help
bin/byteworker commands list --json
bin/byteworker commands describe source.capture --json
bin/byteworker commands search "digest" --json
```

命令名、入口、使用者、可见性、副作用、运行环境和文档位置以 `lib/command_registry.py` 为准。
顶层帮助优先列出分组路径，如 `digest flow`、`kb query` 和 `automation dreaming`。
原有的 `digest-flow`、`kb-query` 和 `dreaming` 等路径仍可用。Agent 按 manifest 的
`preferred_path` 和 `aliases` 选择路径。查看全部兼容入口，包括默认隐藏的 tombstone，用
`bin/byteworker --help --all`。

给用户展示功能时用 `references/capabilities.json` 定义的目标、例句和建议。Agent 通过
`discover status/recommend/feedback` 查看功能并记录本地提示频率。

registry 中 `execution=facade` 的 tool 调用输出 `byteworker-cli/v1` JSON envelope：

```json
{
  "status": "success",
  "data": {},
  "error": null,
  "context": {
    "protocol": "byteworker-cli/v1",
    "tool": "kb-query",
    "operation": "search"
  }
}
```

`status` 的含义：

| 状态 | 含义 |
|---|---|
| `success` | 命令正常完成 |
| `attention` | 命令完成，但发现需要关注的问题，例如 doctor warning |
| `error` | 参数、授权、数据或执行失败；查看 `error.code/message/hint/details` |

### 人工排障

可以直接运行对应 Python CLI，例如：

```bash
python3 bin/digest-txn.py --help
python3 bin/source.py capture --help
python3 bin/doctor.py scan --format text
```

直接入口的输出和退出码属于各工具自己的协议，不保证带统一 envelope。

### Shell 集成

抓取群聊、浏览知识库、修复 links、检查依赖和自动更新等操作保留独立 shell 入口。
需要保证与 preflight 相同的 Python/Node/PATH 时，用
`bin/byteworker run <command> ...`；直接飞书 CLI 用 `bin/byteworker lark ...`，不要猜 NVM
路径。INDEX 重建已有 `index rebuild` 机器入口，`rebuild-index.sh` 继续供人工排障。

## 2. 公共约定与安全边界

示例中使用：

```bash
BYTEWORKER_ROOT=/absolute/path/to/byteworker
BYTEWORKER_KB=/absolute/path/to/byteworker_kb
cd "$BYTEWORKER_ROOT"
```

- 很多命令默认从根目录 `.kbconfig` 第一行读取知识库路径；显式 `--kb` 会覆盖它。
- `kb-query.py` 要求显式提供知识库路径。
- capture、SourceBundle、DigestPlan、backfill plan 和候选节点可能含业务内容，只能放系统临时
  目录或知识库目录，不能放 byteworker skill 仓库。
- 知识库是独立本地 Git 仓库，禁止配置 remote，禁止 push。
- 修改命令前先读 `docs/development/ARCHITECTURE.md`。模块、信息流或跨层契约变化时，必须在同一变更中
  同步架构文档和测试。
- 不确定命令参数时，优先运行对应的 `--help`，不要猜测。

## 3. 命令总览

| 文件 | 主要使用者 | 用途 | 写入影响 |
|---|---|---|---|
| `byteworker` | Agent / 自动化 | 稳定 Python bootstrap 与 runtime-safe launcher | 取决于子命令 |
| `byteworker-launcher.py` | 内部入口 | preflight、机器 CLI、lark/meegle/run 分发 | 取决于子命令 |
| `session-preflight.py` | Agent / 自动化 | 每 session 一次合并启动检查 | 更新 skill 状态、自动报告设置检查 |
| `byteworker-cli.py` | Agent / 自动化 | 统一 JSON 机器协议 facade | 取决于下游工具 |
| `discover.py` | Agent | status 能力地图；complete 交互收尾；diagnose 只读不推荐原因；feedback 拒绝或延后 | complete/recommend 需 --interactive 才写 KB 已排除的 `state/capability_discovery.json`；diagnose 不写 |
| `digest-txn.py` | Agent / 维护者 | digest 预检、校验、原子写入 | `execute` 写 KB 并创建本地 commit |
| `digest-flow.py` | Agent / 维护者 | 自动编排无语义 digest 阶段并保存可恢复 checkpoint | 写 KB 已排除的 `state/digest/flows/`；commit 委派事务 |
| `workflow-budget.py` | Agent / 维护者 | 展开完整 workflow 闭包并计算静态/动态 token 预算 | 只读规则与显式输入，返回方法、计数和固定 overflow action |
| `digest-run.py` | Agent / 维护者 | 单输入全流程阶段、计数和耗时日志 | 写 KB 已排除的 `state/digest/run-logs/` |
| `digest-analysis.py` | Agent / 维护者 | SourceBundle 单次结构去噪与分析 packet | 只写显式系统临时输出，不写 KB truth |
| `digest-capture.py` | Agent / 维护者 | 最多 4 路执行已声明的只读 lark/comments 抓取 job | 原子写显式私密临时输出，不写 KB truth |
| `digest-parallel.py` | Agent / 维护者 | dependency/semantic/conflict 阈值分片、coverage 校验与结果归并 | 只写显式私密临时 shard/result reduce packet |
| `source.py` | Agent / 维护者 | 从 operation adapter 声明生成参数；应用操作与持久化委派 `source_cli_service.py` | capture/Bundle 写输出；Profile 操作可写 KB |
| `wiki.py` | Agent / 维护者 | 按需探索 Wiki 空间/子树并筛选页面 | 写可重建树状态、候选文件或子树 Profile |
| `digest-job.py` | Agent / 维护者 | 管理已确认多页 digest 的可恢复任务 | 写本地任务 checkpoint，不写 raw/节点 |
| `kb-query.py` | Agent / 维护者 | 节点、证据和结构化 raw 查询 | 只读 |
| `doctor.py` | Agent / 维护者 | 扫描 schema/graph/profile 漂移，准备 Digest 反馈 | `fix` 可写 INDEX/links；`bundle` 仅写私有系统临时 ZIP |
| `provenance-backfill.py` | Agent / 维护者 | 历史 raw 出处和证据回填 | `apply` 写 KB 并创建本地 commit |
| `report-automation.py` | Agent / 自动化 | 自动报告设置状态、跨任务租约和真实运行回执 | 写 KB 已排除的 `state/` |
| `dreaming.py` | Agent / 自动化 | 组合 `lib/dreaming_cli_*.py` 的 schedule/process/review/report/action leaf handlers | 私密 state、`run-logs/` 与 KB 外评估指标 |
| `viewer-server.py` | `browse.sh` 内部 | 本地 Viewer 静态服务、受会话访问控制的设置、看板和 Dreaming 日志接口 | 只通过 `settings.py` 写受控配置 |
| `index.py` | Agent / 自动化 | INDEX 重建预演与执行的机器回执 | apply 写 INDEX、journal 和本地 commit |
| `kb-mutate.py` | Agent / 自动化 | 非 digest 内容的版本化事务写入及旧看板迁移 | 写目标、按需更新 INDEX、追加 journal 并创建本地 commit |
| `context.py` | Agent / 自动化 | 按 intent 读取有限 context 投影 | 只读 |
| `dashboard.py` | Agent / 自动化 | 按需生成看板或读取报表共用的手动设置 | 只读 |
| `semantic.py` | Agent / 自动化 | 校验 IM 等结构化语义决定 | 只读临时结果 |
| `inbox.py` | 兼容调用方 | 已移除 Inbox 的稳定 tombstone | 只输出 `INBOX_REMOVED`，无业务副作用 |
| `pull_doc_comments.py` | Agent / 维护者 | 拉取飞书文档全部评论和回复 | 只向 stdout 输出 JSON |
| `pull-chat.sh` | Agent / 维护者 | 拉取群聊窗口逐字稿和 locator | 写临时或显式输出文件 |
| `resolve-users.sh` | Agent / 维护者 | open_id 批量解析为身份与当前通讯录画像 | 只读外部通讯录 |
| `browse.sh` | 用户 | 启动本地只读 Viewer | 只写临时服务目录 |
| `check-deps.sh` | 用户 / 安装流程 | 用同一 resolver 检查运行依赖 | 只读 |
| `rebuild-index.sh` | Agent / 维护者 | 从真相源重建 INDEX | 默认写 `INDEX.md` |
| `rebuild_index.py` | 内部执行器 | INDEX 重建核心实现 | 同上 |
| `repair-links.sh` | 维护者底层排障 | 修复双向 links、自链接和重复项 | 默认写节点 frontmatter |
| `repair_links.py` | 内部执行器 | links 修复核心实现 | 同上 |
| `update-check.sh` | Skill 自动调用 | 周期性 fast-forward 更新 | 可更新 skill；更新后可能修复 KB |
| `update-postflight.py` | 内部更新流程 | 更新后 doctor 与安全修复 | 可写 KB 并创建本地 commit |
| `update-state.py` | 内部更新流程 | 更新检查和退避状态机 | 写 `.update-state.json` |

## 4. `byteworker-cli.py`：统一机器协议

统一 Python 工具的单行 JSON 响应格式，区分成功、需关注和错误。

```bash
bin/byteworker [--pretty] <tool> [tool arguments...]
```

常用示例：

```bash
bin/byteworker --pretty \
  kb-query search \
  --kb "$BYTEWORKER_KB" \
  --query "OCR 2.0"

bin/byteworker \
  source auth-status \
  --source-type meego \
  --host project.feishu.cn

bin/byteworker update-status

bin/byteworker report-automation status \
  --kb "$BYTEWORKER_KB"

bin/byteworker dreaming status \
  --kb "$BYTEWORKER_KB"
```

细节：

- `--pretty` 只改变 envelope 的缩进，不改变 `data` 语义。
- 下游退出码仍作为 facade 的退出码返回。
- doctor 的退出码 `2` 会映射为 `status=attention`，不是传输失败。
- 下游的结构化 `error.code/message/hint/details` 会尽量原样保留。
- stderr 会被截断后放入 `error.details`，不会把完整命令参数或正文复制进协议。
- stdout 不超过 1 MiB 时保持 inline；超过后返回 `byteworker-cli-artifact/v1` 的 `0600` 临时
  artifact receipt，调用方校验 hash、读取并删除。支持 `--out` 的命令仍应优先显式写 artifact。
- `context.command_path/stability/side_effect` 来自命令 registry，不从 argv 或文案猜测。
- 任意层级 `-h/--help` 都在 runtime 探测和业务执行前走只读旁路，输出普通文本而非 envelope。
- launcher 原生命令和外部 passthrough 不伪装成 facade；以 `commands describe` 的
  `execution/output_protocol` 为准。

## 5. `digest-run.py`：端到端摄取耗时

收到输入就调用 `start`。每个 Agent 阶段都记录 `stage started`，结束时记录 `stage completed`
或 `stage failed`。宿主拿到模型用量后调用 `usage`，最后调用 `complete`。

用 `wait/resume` 排除等待用户的时间。未结束的运行连续 6 小时没有 lifecycle heartbeat 时显示
stale，活跃耗时只算到最后一个事件。`list/show` 返回活跃耗时、最慢阶段和 token 汇总。
阶段协议和隐私边界见 `references/digest-observability.md`。

```bash
bin/byteworker digest-run start --kb "$BYTEWORKER_KB"
bin/byteworker digest-run usage --kb "$BYTEWORKER_KB" --run-id "$DIGEST_RUN_ID" \
  --stage semantic_analysis --worker-role coordinator --usage-source measured \
  --call-id "$MODEL_CALL_ID" --input-tokens 1200 --cached-input-tokens 900 \
  --output-tokens 150 --reasoning-tokens 40
bin/byteworker digest-run list --kb "$BYTEWORKER_KB" --limit 20
bin/byteworker digest-run show --kb "$BYTEWORKER_KB" --run-id "$DIGEST_RUN_ID"
```

## 5A. `digest-flow.py`：确定性生命周期编排

标准 digest 先 `start`，再按需分批 capture。`prepare` 一次完成 Bundle、preflight、analysis packet
及依赖和语义规划，最后由 `commit` 调用事务。所有 request 和 plan 放入返回的私有 `work_dir`。
中断后用 `status` 恢复。

```bash
bin/byteworker digest-flow start --kb "$BYTEWORKER_KB" --source-type local_md
bin/byteworker digest-flow status --kb "$BYTEWORKER_KB" --run-id "$DIGEST_RUN_ID"
```

## 5B. `workflow-budget.py`：完整 workflow token 预算

`resolve` 只返回本次来源/feature 的规则路径，Agent 无需整读 manifest；resolver 不计算 token、不访问 KB：

```bash
bin/byteworker workflow-budget resolve --workflow digest --source-type feishu_doc
```

普通路径加载 core/flow/inline 与安全语义 policy；parallel_execution/recovery/batch 等条件触发时
再 resolve 并补读新增文件。已有 inspect 和完整 worker 闭包仍保留。

```bash
bin/byteworker workflow-budget inspect --workflow digest \
  --source-type feishu_doc --feature comments \
  --context "$CONTEXT_PROJECTION" --source-packet "$ANALYSIS_PACKET"
```

回执分离静态规则、worker prompt、context、source packet、总输入与输出预算；没有固定 tokenizer 时
明确返回带版本的保守估算。`over_budget` 只给固定 action，不截断文件。

## 5C. `digest-analysis.py`：一次性分析预处理

在 SourceBundle preflight 非 noop 后，把正文、评论、白板、参与者和 anchors 一次整理成系统临时
analysis packet；stdout 只返回 path/hash 和计数，业务文本只存在权限 `0600` 的 packet 中。

```bash
bin/byteworker digest-analysis prepare \
  --kb "$BYTEWORKER_KB" \
  --bundle /tmp/byteworker-example/source-bundle.json \
  --out /tmp/byteworker-example/analysis-packet.json \
  --run-id "$DIGEST_RUN_ID"
```

相同输入 hash 和输出路径会命中 packet cache。输出路径位于 skill 仓库时拒绝执行；传 `--run-id`
时自动记录 `analysis_prepare` 的开始、完成或失败。

## 5D. `digest-capture.py`：有界只读抓取

接收系统临时 `byteworker-digest-capture-plan/v1`，最多 4 路执行独立 lark/comments job。每个 stdout
原子写入显式 `0600` 输出；回执只含 job 状态、耗时、重试和字节数，不含正文。

```bash
bin/byteworker digest-capture execute --request "$CAPTURE_PLAN"
```

## 5E. `digest-parallel.py`：语义分片与归并

按固定阈值为 dependency、semantic、conflict 生成最多 4 个私密 shard；worker 结果必须完整覆盖
plan，merge 才生成 reducer packet。工具只校验和分片，不做语义裁决。

```bash
bin/byteworker digest-parallel plan --stage semantic --input "$PACKET" --out-dir "$WORK"
bin/byteworker digest-parallel merge --plan "$PLAN" --result "$R1" --result "$R2" --out "$REDUCE"
```

## 5F. `digest-txn.py`：摄取事务

`txn` 是 transaction 的缩写。该工具保证一次摄取要么完整写入，要么回滚，不留下半成品。

推荐通过机器协议调用：

```bash
bin/byteworker digest-txn <subcommand> ...
```

### `preflight`

读取 SourceBundle 或 DigestPlan，计算 payload/hash，并判断同源状态；不写知识库。

```bash
bin/byteworker digest-txn preflight \
  --kb "$BYTEWORKER_KB" \
  --manifest /tmp/byteworker-example/digest-plan.json \
  --run-id "$DIGEST_RUN_ID"
```

典型状态包括：

- `new_source`
- `new_version`
- `noop`
- `resume_failed`

### `snapshot-node`

读取已有节点并返回 `base_sha256`，供 update candidate 做乐观并发保护；只读。

```bash
bin/byteworker digest-txn snapshot-node \
  --kb "$BYTEWORKER_KB" \
  --path knowledge/projects/project-ocr.md
```

### `validate`

完整校验 plan、Bundle、候选节点、links、evidence、baseline 和路径安全；不写知识库。

```bash
bin/byteworker digest-txn validate \
  --kb "$BYTEWORKER_KB" \
  --manifest /tmp/byteworker-example/digest-plan.json \
  --run-id "$DIGEST_RUN_ID"
```

### `execute`

拿写锁后重新执行 preflight 和 baseline 校验，再原子写入：

- `raw_data/`
- `provenance/`
- `knowledge/`
- `INDEX.md`
- `journal/`
- 本地 Git commit

```bash
bin/byteworker digest-txn execute \
  --kb "$BYTEWORKER_KB" \
  --manifest /tmp/byteworker-example/digest-plan.json \
  --run-id "$DIGEST_RUN_ID"
```

只有返回 `data.status=committed` 和 commit hash 才表示真实完成。候选文件生成、validate 通过或
目标文件暂时出现，都不能当成成功。

安全约束：

- manifest、Bundle、component 和 candidate 不得位于 skill 仓库。
- KB 配有 remote、有预先 staged 变更、baseline 变化或路径越界时拒绝写入。
- 支持 `digest-plan/v1`、`digest-plan/v2`、`digest-batch-plan/v1` 和
  `digest-batch-plan/v2`。
- 新的单来源流程使用 `SourceBundle v2 + DigestPlan v2`。
- 新的多来源流程使用多个 `SourceBundle v2 + digest-batch-plan/v2`；v1 只兼容历史调用。

模板：

- `templates/source-bundle-v2.json`
- `templates/digest-plan-v2.json`
- `templates/digest-plan-v1.json`
- `templates/digest-batch-plan-v2.json`
- `templates/digest-batch-plan-v1.json`

## 6. `source.py`：统一来源入口

用途：统一来源 capability、Profile、抓取和 SourceBundle 出口。provider 的 transport
保持异构，但进入 digest 的单来源结果都必须是 `SourceBundle v2`。

```bash
bin/byteworker source <subcommand> ...
```

### `capabilities`

列出三个正交集合：可执行 operation、可保存 Profile、可生成 Bundle 的来源类型。

```bash
bin/byteworker source capabilities
```

### `bundle-spec`

输出 adapter 的机器可读 request 契约；字段由实际 builder 签名推导，另含 artifact/component
形状、source UID 规则和最小示例：

```bash
bin/byteworker source bundle-spec \
  --source-type feishu_minutes
```

### `auth-status`

无副作用检查登录和最小授权，不主动发起 OAuth。

```bash
bin/byteworker source auth-status \
  --source-type meego \
  --host project.feishu.cn
```

支持 `meego`、`feishu_base`、`aeolus`、`feishu_chat`。群聊只检查共享 Lark 用户身份，
具体 IM scope 在 capture 时 fail closed 验证。

### `inspect`

读取来源元数据并验证 URL、项目、表、视图、字段、报表和筛选坐标；不抓完整业务快照。

```bash
bin/byteworker source inspect \
  --source-type meego \
  --url "<meego-view-url>" \
  --field name \
  --field status \
  --field updated_at
```

Meego 空间主页只做 URL 类型识别，并提示提供具体 Story View URL：

```bash
bin/byteworker source inspect \
  --source-type meego \
  --url "<meego-project-home-url>"
```

主要参数：

- 通用：`--source-type`、`--url`、`--timeout`
- Meego：`--project-key`、`--view-id`、可重复 `--field`；空间主页返回
  `SOURCE_SELECTION_REQUIRED`
- Base：`--base-token`、`--table-id`、`--view-id`
- Aeolus：用 `--url` 检查 dashboard。报表和筛选选项留给 `capture` / `register`。

### `capture`

完整分页抓取并生成规范快照或 Bundle。

按已保存 Profile 抓取：

```bash
bin/byteworker source capture \
  --source-type meego \
  --kb "$BYTEWORKER_KB" \
  --source-uid "<stable-source-uid>" \
  --out /tmp/byteworker-example/current-capture.json \
  --bundle-out /tmp/byteworker-example/current-bundle.json
```

临时检查也可直接提供 URL/坐标/字段。已有 Profile 的 routine 流程必须重放 Profile，不得从最近
raw 猜参数。

Meego/Base/Aeolus 的 `--bundle-out` 必须与 `--out` 同时使用：前者保留兼容 capture，后者是
统一 digest 交接。群聊必须按 v2 Profile capture，`--out` 直接写 Bundle，并把逐字稿/locator
写到 Bundle 旁的业务临时目录。

`--out` 必须位于系统临时目录或知识库目录。未提供时结构化命令返回 capture JSON；大快照建议
总是提供输出文件。
需要统一的 Digest 输入时用 `source capture --bundle-only --out <bundle.json>`。各来源都返回
Bundle，仍需保留其旁边的私有材料目录。按来源支持的参数见 `source capabilities` 的
`arguments_by_source_type`，也可用 `commands describe source.capture --json` 查看。

### `bundle`

把宿主已经抓取的文档、妙记、Web、本地资料，或现有结构化 capture，通过 registry 构造成
严格 `SourceBundle v2`：

```bash
bin/byteworker source bundle \
  --source-type web \
  --request /tmp/byteworker-example/web-request.json \
  --out /tmp/byteworker-example/web-bundle.json
```

`--request` 只接受 request JSON **文件路径**，不接受内联 JSON。request 是 adapter 自己严格
校验的 provider 参数，只能放临时目录或 KB，且不得重复声明
`source_type`。结构化 capture 只传 `capture_path`；CLI 拒绝内联 `capture`，避免路径和
内联内容成为两份互相矛盾的真相。当前 Bundle adapter：`aeolus`、`feishu_base`、`feishu_chat`、`feishu_doc`、
`feishu_minutes`、`local_md`、`meego`、`web`。

`capture --out ... --bundle-out ...` 的两个路径必须不同。命令会先完成 Bundle 校验，再暂存
两个 JSON；若第二个文件替换失败，会恢复第一个文件的原内容。

常用 request 形状：

| 来源 | request 关键字段 |
|---|---|
| Meego / Base / Aeolus | `{"capture_path":"/abs/capture.json"}` |
| 飞书文档 | `source_uid/source_url/title/revision/body/comments?/whiteboards?/anchors?/provider_metadata?` |
| 群聊 | `source_uid/title/source_window/transcript`；`source_url/locator_artifact` 可选，routine 优先直接用 Profile capture |
| 妙记 | `source_uid/source_url/title/transcript/anchors?` |
| Web | `source_uid/source_url/title/body/anchors?` |
| 本地 Markdown | `source_uid/title/local_file/anchors?` |

component 参数如 `body`、`transcript`、`local_file` 使用
`{"path":"/absolute/business-file"}`；合法 request 字段是
`name/kind/path/mode/json_pointer/heading/uid/media_type/coverage`。JSON wrapper 中的文本可用
`mode:"verbatim" + json_pointer`，结构化值用 `mode:"canonical-json" + json_pointer`。
完整契约以 `references/machine-protocol.md`「SourceBundle request 快速参考」和
`source bundle-spec` 输出为准。

### `register`

实时 inspect 后，把一个 Aeolus dashboard sheet 保存为独立 v1 Profile，重建 INDEX 并创建
知识库本地 commit。

```bash
bin/byteworker source register \
  --source-type aeolus \
  --kb "$BYTEWORKER_KB" \
  --url "<aeolus-sheet-url>" \
  --routine weekly
```

当前 `register` 只支持 Aeolus。Meego、Base、群聊和飞书文档 v2 Profile 使用 `profile-save`。

### `profile-save`

严格校验临时 Profile JSON，原子写入 `sources/`，重建 INDEX，并创建本地 commit。

```bash
bin/byteworker source profile-save \
  --kb "$BYTEWORKER_KB" \
  --file /tmp/byteworker-example/source-profile.json
```

Profile 不得包含 token、cookie、JWT、密码或抓取结果。

### `profile` / `profiles`

读取一个 Profile，或列出全部 Profile；只读。

```bash
bin/byteworker source profile \
  --kb "$BYTEWORKER_KB" \
  --source-uid "<stable-source-uid>"

bin/byteworker source profiles \
  --kb "$BYTEWORKER_KB" \
  --source-type meego
```

`profiles --source-type` 当前支持 `aeolus`、`feishu_base`、`feishu_chat`、`feishu_doc`、
`meego`。

### `diff`

按稳定记录 ID 比较当前 capture 与上一份完整快照。

显式提供两份 capture：

```bash
bin/byteworker source diff \
  --previous /tmp/byteworker-example/previous.json \
  --current /tmp/byteworker-example/current.json \
  --out /tmp/byteworker-example/diff.json
```

从 KB 自动选择上一份已提交快照：

```bash
bin/byteworker source diff \
  --kb "$BYTEWORKER_KB" \
  --source-uid "<stable-source-uid>" \
  --current /tmp/byteworker-example/current.json
```

可用 `--raw-id` 或 `--history-index` 显式选择历史版本。`left_view` 仅表示记录离开当前视图，
不等于删除或取消。

## 6A. `wiki.py` 与 `digest-job.py`：按需 Wiki 工作流

两个入口只在用户探索飞书知识库空间或恢复多页 digest 时使用。`byteworker-cli.py` 以子进程
调用它们，普通 digest/query 路径不会 import Wiki 模块、检查授权、扫描状态或创建目录。

`wiki.py` 的主要操作：

```bash
bin/byteworker wiki auth-status
bin/byteworker wiki inspect --url "<Wiki URL>"
bin/byteworker wiki scan \
  --kb "$BYTEWORKER_KB" --url "<Wiki URL>" --max-nodes 20000
bin/byteworker wiki scan \
  --kb "$BYTEWORKER_KB" \
  --source-uid "feishu_wiki:<space_id>:<root_node_token>"
bin/byteworker wiki topics \
  --kb "$BYTEWORKER_KB" --space-id "<space_id>" --limit 30
bin/byteworker wiki candidates \
  --kb "$BYTEWORKER_KB" --space-id "<space_id>" \
  --root-node-token "<node_token>" \
  --updated-after "2026-01-01T00:00:00+08:00" \
  --out "<临时目录>/wiki-selection.json"
bin/byteworker wiki profile-create \
  --kb "$BYTEWORKER_KB" --url "<Wiki URL>" \
  --root-node-token "<node_token>" \
  --routine weekly --change-detection structure_only
```

API 始终显式 `--as user`。全空间从真实 `space_id` 根节点列表开始；完整树仅保存到
`<KB>/state/wiki/`，没有 TTL，失败或深度截断时不替换旧状态。`topics` 与 `candidates` 只在
stdout 返回有限预览。子树 Profile 使用 `feishu_wiki:<space_id>:<root_node_token>`，只描述目录
监控；被选页面仍分别按 `feishu_doc` digest。

用户确认候选文件后，用 `digest-job.py` 创建任务并分批领取：

```bash
bin/byteworker digest-job create \
  --kb "$BYTEWORKER_KB" --selection "<临时目录>/wiki-selection.json" \
  --title "检索知识库" --batch-size 5
bin/byteworker digest-job list --kb "$BYTEWORKER_KB" --active
bin/byteworker digest-job status \
  --kb "$BYTEWORKER_KB" --job-id "<job_id>" --limit 20
bin/byteworker digest-job next \
  --kb "$BYTEWORKER_KB" --job-id "<job_id>" \
  --limit 5 --lease-owner "<session_id>" --lease-seconds 1800
bin/byteworker digest-job mark \
  --kb "$BYTEWORKER_KB" --job-id "<job_id>" \
  --document-id "<document_id>" --status committed \
  --raw-id "<raw_id>" --commit "<commit>"
bin/byteworker digest-job reconcile \
  --kb "$BYTEWORKER_KB" --job-id "<job_id>"
bin/byteworker digest-job cancel \
  --kb "$BYTEWORKER_KB" --job-id "<job_id>"
```

任务写在 `<KB>/state/digest_jobs/`，保存页面身份、状态、有限时租约和 receipt 定位，不保存正文
或凭据。租约过期可由新 session 领取；`reconcile` 只读 committed raw，恢复事务已成功但任务
尚未标记的页面。

## 6B. `report-automation.py`：自动报告状态与执行租约

该工具不创建、唤醒或删除 Codex / Claude / TRAE 任务。它只在知识库
`state/report_automation.json` 中记录一次性设置选择、当前 prompt 版本、跨日报/周报单租约、
`last_attempt/last_run/last_success` 和真实运行结果；宿主任务列表始终是真相源。

```bash
# 首次安装或升级迁移是否需要询问
bin/byteworker report-automation status --kb "$BYTEWORKER_KB"

# 展示问题前先标记已询问；用户拒绝或稍后再改为对应值
bin/byteworker report-automation decision \
  --kb "$BYTEWORKER_KB" --value prompted

# 任务真实创建且 Run now 通过后才记录
bin/byteworker report-automation configure \
  --kb "$BYTEWORKER_KB" \
  --harness codex \
  --timezone Asia/Shanghai \
  --environment local \
  --daily-schedule "工作日 20:30" \
  --weekly-schedule "周一 09:30" \
  --recovery-schedule "每天 08:30/12:30/18:30/22:30" \
  --recovery-task-id "<recovery_task_id>"

# 补偿任务先检查指定 period；只有 due + should_run=true 才补跑
bin/byteworker report-automation check \
  --kb "$BYTEWORKER_KB" \
  --kind daily \
  --period 2026-07-30

# 每次运行先领取租约；日报 period 用 YYYY-MM-DD，周报用 YYYY-Www
bin/byteworker report-automation lease \
  --kb "$BYTEWORKER_KB" \
  --kind daily \
  --period 2026-07-30 \
  --owner codex

# 成功必须提供 reports/<kind>/ 下的报告路径；失败必须提供稳定错误码
bin/byteworker report-automation complete \
  --kb "$BYTEWORKER_KB" \
  --token "<lease token>" \
  --run-status success \
  --report-path reports/daily/2026-07-30.md
```

状态目录会加入知识库 `.git/info/exclude`。自动报告只允许 `local` 环境；租约过期后可恢复，
有效租约存在时返回 `REPORT_AUTOMATION_BUSY`，调用方必须安全退出，不能并发写同一 KB。
`check` 返回 `due/complete/busy/disabled`，不会写报告或领取租约。

## 7. `kb-query.py`：确定性知识查询

全部子命令只读。

### `search`

对节点正文、frontmatter 和 INDEX 做有限召回，并最多扩展一跳 links。

```bash
bin/byteworker kb-query search \
  --kb "$BYTEWORKER_KB" \
  --query "OCR 2.0" \
  --limit 12 \
  --graph-depth 1 \
  --max-nodes 30
```

- `--graph-depth` 当前只支持 `0` 或 `1`。
- `--limit` 控制初始结果数。
- `--max-nodes` 控制扩图后的总节点上限。

### `conflict-search`

语义分析先把最多 32 条候选事实写入系统临时 `byteworker-conflict-query/v1`，再一次扫描 KB 完成
同源精确定位与多 query 有界召回。它只返回 TL;DR 和短 snippet，不做冲突裁决。

```bash
bin/byteworker kb-query conflict-search \
  --kb "$BYTEWORKER_KB" \
  --request /tmp/byteworker-example/conflict-query.json \
  --limit-per-query 3 \
  --max-nodes 20
```

request 可能包含业务事实，必须位于系统临时目录或 KB，不能写进 skill 仓库。

### `evidence`

解析节点中的 `[E1]` 等标记，返回对应 raw、anchor、open URL 和 source metadata。

```bash
bin/byteworker kb-query evidence \
  --kb "$BYTEWORKER_KB" \
  --node event-2026-07-29-ocr-weekly \
  --markers E1,E2
```

省略 `--markers` 时返回节点全部证据。

### `source-record`

从 Meego、Base、Aeolus 最新完整 raw 快照中按稳定 ID 或标题查普通结构化记录。

```bash
bin/byteworker kb-query source-record \
  --kb "$BYTEWORKER_KB" \
  --source-type meego \
  --record-id "<work-item-id>"

bin/byteworker kb-query source-record \
  --kb "$BYTEWORKER_KB" \
  --source-uid "<stable-source-uid>" \
  --title "安全审核基座" \
  --limit 5
```

- 默认每个 `source_uid` 只查最新快照。
- `--history` 才会同时查询历史版本。
- `--title-threshold` 默认 `0.55`，控制模糊标题匹配门槛。
- 优先使用 `byteworker-record-index/v1`；旧 raw 才走 provider 兼容投影。

## 8. `doctor.py`：兼容性诊断

### 只读扫描

```bash
bin/byteworker doctor scan \
  --kb "$BYTEWORKER_KB"
```

或人工阅读文本：

```bash
python3 bin/doctor.py scan \
  --kb "$BYTEWORKER_KB" \
  --format text
```

### 确定性修复

```bash
bin/byteworker doctor fix \
  --kb "$BYTEWORKER_KB" \
  --only index,links \
  --dry-run
```

去掉 `--dry-run` 才真正写入。`--autolink` 会把正文提及的已存在节点 ID 补进 links。apply
使用共享 KB 写锁，统一写 journal、精确暂存并创建本地 commit；失败整体回滚。

Doctor 还会只读检查 Profile v1/v2、定期来源的 Profile 覆盖、raw/Profile identity、
payload component/digest key 和规范记录索引。Meego/Base/Aeolus/群聊缺 Profile 会报 error，飞书文档
legacy routine 报 warning，尚无 Profile schema 的来源报兼容 info。

Doctor 只自动处理可证明的 INDEX/links 问题；缺失 Profile、业务字段、悬空 ID、证据缺失、
重复 ID 或损坏真相源只报告，不猜写。Profile 迁移必须先人工复核 identity/capture policy，
再显式调用 `source profile-save` 或 `source register`。

直接入口退出码：

| 退出码 | 含义 |
|---|---|
| `0` | 扫描干净，或修复后无 error/warning |
| `1` | 参数、执行或修复失败 |
| `2` | 扫描完成，但仍有 error/warning |

## 9. `provenance-backfill.py`：历史出处回填

这是一个显式的四步流程，不能跳过 validate 直接把未复核计划当成安全事实。

### `audit`

只读扫描缺少 provenance/primary source/evidence 的历史 raw 和节点。

```bash
bin/byteworker provenance-backfill audit \
  --kb "$BYTEWORKER_KB"
```

### `plan`

生成候选回填计划，不应用。

```bash
bin/byteworker provenance-backfill plan \
  --kb "$BYTEWORKER_KB" \
  --output /tmp/byteworker-example/provenance-backfill.json
```

### `validate`

检查经 Agent 或用户复核后的计划；不写知识库。

```bash
bin/byteworker provenance-backfill validate \
  --kb "$BYTEWORKER_KB" \
  --plan /tmp/byteworker-example/provenance-backfill.json
```

### `apply`

加锁并写入 sidecar、候选节点和 journal，精确暂存后创建知识库本地 commit；失败整体回滚。

```bash
bin/byteworker provenance-backfill apply \
  --kb "$BYTEWORKER_KB" \
  --plan /tmp/byteworker-example/provenance-backfill.json
```

Plan 和 candidate 只能位于系统临时目录或知识库目录。

## 10. 来源抓取辅助命令

### `pull_doc_comments.py`

抓取飞书文档的全部评论、已解决评论、完整回复链和正文锚点，输出 canonical JSON。

```bash
bin/byteworker run bin/pull_doc_comments.py \
  --url "<feishu-doc-or-wiki-url>" \
  --as user \
  --pretty \
  > /tmp/byteworker-example/comments.json
```

- `--as` 支持 `user` 或 `bot`，默认 `user`。
- 分页或回复链不完整时 fail closed。
- 只向 stdout 输出，不直接写知识库。

### `pull-chat.sh`

按群名或 `chat_id` 拉取指定时间窗的完整群聊逐字稿和精确 message locators。

首次显式窗口：

```bash
bin/byteworker run bin/pull-chat.sh \
  --query "<exact-chat-name>" \
  --start "2026-07-29T00:00:00+08:00" \
  --end "2026-07-29T18:00:00+08:00" \
  --out /tmp/byteworker-example/chat.txt \
  --locators-out /tmp/byteworker-example/chat-locators.json
```

已有历史 raw 后增量续拉：

```bash
bin/byteworker run bin/pull-chat.sh \
  --chat-id "<oc_xxx>" \
  --since-last \
  --out /tmp/byteworker-example/chat.txt
```

stdout 末尾输出 `chat_id/messages/pages/window/transcript/locators` 等摘要。

关键退出码：

| 退出码 | 含义 |
|---|---|
| `2` | 群未找到 |
| `3` | 群名匹配多个群，需改用 `--chat-id` |
| `4` | `--since-last` 没有历史窗口 |
| `5` | 达到页数上限，结果可能截断；不得继续摄取 |

### `resolve-users.sh`

把 `ou_...` open_id 批量解析为姓名、企业 `feishu_id`、邮箱和当前部门路径。

```bash
bin/byteworker run bin/resolve-users.sh --from-doc /tmp/byteworker-example/chat.txt
bin/byteworker run bin/resolve-users.sh --ids ou_xxx,ou_yyy
printf '%s\n' ou_xxx ou_yyy | bin/byteworker run bin/resolve-users.sh
bin/byteworker run bin/resolve-users.sh --ids ou_xxx,ou_yyy --format json
```

默认 stdout 保留旧三列 TSV，供已有调用继续使用：

```text
<open_id>    <姓名>    <feishu_id>
```

`--format json` 输出版本化的 `byteworker-resolved-users/v1`：

```json
{
  "schema_version": "byteworker-resolved-users/v1",
  "resolved_at": "2026-07-30T17:20:00+08:00",
  "users": [
    {
      "open_id": "ou_xxx",
      "name": "姓名",
      "feishu_id": "email-prefix",
      "email": "email-prefix@example.com",
      "enterprise_email": "email-prefix@example.com",
      "department_path": "一级部门-二级团队",
      "is_activated": true,
      "is_cross_tenant": false
    }
  ]
}
```

person 新建/更新必须使用 JSON 模式：`resolved_at` 写入 `directory_verified_at`，可见的
`enterprise_email` / `department_path` 同步到节点。部门为空不代表调动，不得清空已有非空值。
身份字段解析不到时使用 `?`；可选通讯录字段不可见时为空字符串；进度写 stderr。需要
`lark-cli`、`jq` 和用户态通讯录授权。

### `dreaming.py`

Dreaming 启用前先用 `configure` 选择 process 的 `interval`、`daily_time` 或 `every_n_days`
策略，并确认 morning/maintenance/recovery 与日志保留期。`enable` 需要能力、schedule、机器条件
三项确认。启用后 `harness.status=pending`；宿主任务真实创建后才运行
`harness register --task-id ...`，此后 `operational=true`。

每次后台 lease 都有 `run_id`。runner 用 `heartbeat` 记录阶段，用 `complete` 记录有限计数；
`runs list/show/tail` 查询私有结构化日志。日志不保存消息或 Finding 正文。完整协议见
`references/dreaming.md`。

`process digest-scan` 先检查已启用定期来源，手动来源由 `process digest-source` 打开和预检。
`process digest-prepare` 是只准备清单的兼容入口。完成后使用 `process digest-record/digest-complete`：
runner 仍逐源执行普通 digest，Dreaming 只快照来源、核验 `committed/noop` 或 Wiki 完整扫描
回执，并在全部完成后推进报告 coverage checkpoint。

### `inbox.py`

独立 Inbox 已移除。该入口只为一个 major 版本内的旧调用方返回稳定
`INBOX_REMOVED`，不读取参数、IM、Dreaming state 或 KB，也不创建文件。IM 单次分析使用
`dreaming process once --source im`，持续处理使用显式 Dreaming grant。
- 召回：`--no-chat-list`、`--no-search`、`--queryless-search`、`--no-context-search`
- 提示：`--no-first-run-notice`、`--no-repeat-run-notice`
- `--dry-run`：只展示时间窗、关键词、context 词表和预算，不调用 `lark-cli`

该命令较重，默认建议一天最多运行一次。

## 11. 知识库维护命令

### `rebuild-index.sh`

从以下真相源确定性重建 `INDEX.md`：

- 8 类 `knowledge/` 节点
- `sources/` Profile
- 兼容的 raw routine
- 群聊摄取高水位

Agent / 自动化优先使用机器协议：

```bash
bin/byteworker index rebuild --kb "$BYTEWORKER_KB" --dry-run
bin/byteworker index rebuild --kb "$BYTEWORKER_KB"
```

apply receipt 会返回 journal/Git commit 状态和 commit hash；`--dry-run` 始终只读。下面的
shell wrapper 继续用于人工底层排障。

先预演：

```bash
bin/rebuild-index.sh --kb "$BYTEWORKER_KB" --dry-run
```

实际写入：

```bash
bin/rebuild-index.sh --kb "$BYTEWORKER_KB"
```

脚本只原子替换 `INDEX.md`，不创建 journal 或 Git commit，因此不作为 Agent 的 apply 入口。

### `rebuild_index.py`

`rebuild-index.sh` 的内部 Python 执行器。Agent / 自动化应调用 `index rebuild` facade；
人工排障调用 shell wrapper。

```bash
python3 bin/rebuild_index.py "$BYTEWORKER_KB" [--dry-run]
```

### `repair-links.sh`

检查和修复：

- 双向 links 对称性
- links 去重
- 自链接删除
- 正文中已存在节点 ID 的可选 autolink

悬空链接、重复节点 ID 和格式损坏只报告，不猜写。

```bash
bin/repair-links.sh \
  --kb "$BYTEWORKER_KB" \
  --dry-run \
  --autolink
```

去掉 `--dry-run` 后实际修改节点 frontmatter。命令不创建 journal 或 Git commit，仅供维护者
底层排障；Agent / 自动化使用 `doctor fix --only links [--autolink]` 的完整事务。

退出码 `3` 表示扫描完成但仍有悬空链接，需要人工裁决。

### `repair_links.py`

`repair-links.sh` 的内部 Python 执行器。Agent / 自动化不得直接调用。

```bash
python3 bin/repair_links.py "$BYTEWORKER_KB" [--dry-run] [--autolink]
```

## 12. 浏览与安装检查

### `byteworker` / `session-preflight.py`

Agent 日常只需：

```bash
bin/byteworker preflight --interactive
bin/byteworker preflight --interactive --require feishu
bin/byteworker preflight --unattended
```

- 默认健康路径 stdout/stderr 均为空，退出码 `0`。
- 有自动更新、自动报告迁移或依赖问题时，输出一行
  `byteworker-session-preflight/v1` JSON；只有 blocking 返回非零。
- `--json` 仅供排障，健康时也展示 KB、resolved runtime 与完整检查结果。
- preflight 自动从 `sources/*.json` 推导已经配置的飞书/Meego runtime；`--require` 用于本次
  即将访问但尚未登记的来源。
- 只有 `--interactive` 允许返回低频能力建议；无参数和 `--unattended` 都禁止，适合定时任务。
- `bin/byteworker lark ...`、`bin/byteworker meegle ...` 和
  `bin/byteworker run <command> ...` 继承同一 runtime resolver。显式设置的
  `BYTEWORKER_PYTHON_BIN/BYTEWORKER_NODE_BIN/BYTEWORKER_LARK_CLI_BIN/BYTEWORKER_MEEGLE_BIN`
  若无效会直接报错，不静默换成另一套身份或版本。

### `browse.sh`

在本机启动 Viewer。工作看板由只读接口 `/api/dashboard` 按需生成，其他浏览内容来自静态文件。
设置对话框通过 `viewer-server.py` 的本机会话 API 写入受控配置。

Dreaming 运行日志调试页不加入主界面导航，服务启动后直接访问：

```text
http://localhost:<端口>/app/dreaming-debug.html
```

```bash
bin/browse.sh
bin/browse.sh 8765
```

- 默认端口 `8765`；被占用时自动寻找其它可用端口。
- 只绑定 `127.0.0.1`。
- 临时服务目录只包含 `viewer/` 和知识库目录的符号链接。
- `/api/settings` 默认要求启动时生成的一次性访问口令；用户可在设置页关闭访问保护以提升便捷性。
  无论是否开启口令，服务仍只绑定本机。
- 设置页可以调整浏览器显示偏好、Viewer 访问保护、Dreaming 开关与运行偏好，以及 Source Profile 的定期摄取设置。
- 按 `Ctrl-C` 停止并清理临时目录。

### `check-deps.sh`

检查 byteworker 自身依赖和各内部来源依赖。它委托给 `bin/byteworker deps`，因此安装检查与
运行期 preflight 使用同一套版本探测和 NVM/PATH 发现逻辑。

```bash
bin/check-deps.sh
```

退出码：

| 退出码 | 含义 |
|---|---|
| `0` | Tier 1/2 全部就绪 |
| `1` | Tier 1 必需依赖缺失 |
| `2` | byteworker 基础可用，但内部来源依赖不完整 |

Tier 1 需要 `git`、`jq`、`bash` 和带 `zoneinfo` 的 Python 3.10+。Tier 2 检查 Node、
`lark-cli` 和 `meegle` 能否启动。登录状态用对应来源的 `source auth-status` 检查。
依赖检查不发起 OAuth。

## 13. 自动更新内部命令

这些命令通常由 `SKILL.md` 和 `update-check.sh` 自动调用。手动排查先用 `bin/byteworker update-status`。

### `update-check.sh`

```bash
bin/update-check.sh
bin/update-check.sh --force
```

- 默认成功检查后 7 天内不重复 fetch。
- 失败使用短周期指数退避。
- 用跨进程锁避免并发更新。
- 只允许 fast-forward，不覆盖本地分叉或冲突改动。
- 只有代码 HEAD 真实变化后才触发 postflight doctor。
- `BYTEWORKER_NO_AUTO_UPDATE=1` 可停用。
- 非 `master` 分支或 detached HEAD 直接报错并退出 `1`，不更新、不自动切分支；`--force` 不绕过。
- 其它情况退出 `0`；有输出表示需要把 notice 告诉用户，无输出表示无需处理。

查询当前状态：

```bash
bin/byteworker update-status
```

### `update-postflight.py`

代码真实更新后运行 doctor，自动处理明确声明为低成本、确定性的 INDEX/links 修复。

```bash
python3 bin/update-postflight.py \
  --kb "$BYTEWORKER_KB" \
  --format json
```

它会检查 staged/dirty 改动、重复 ID、损坏的 frontmatter、KB remote 和关系图完整性。
安全修复完成后，追加维护 journal 并创建本地回滚 commit。遇到严重问题时返回待决策状态。

### `update-state.py`

`update-check.sh` 使用的内部状态机，不建议人工修改或直接调用。它维护：

- update 是否到期
- 最近 attempt/success/failure
- 普通更新失败退避
- postflight pending/due/success/failure
- 最近成功 commit

接口：

```text
due
attempt
success
failure
postflight-pending
postflight-due
postflight-success
postflight-failure
status
```

状态文件默认是根目录 `.update-state.json`，由脚本原子维护。人工查看状态应使用：

```bash
bin/byteworker update-status
```

## 14. 修改或新增命令时

1. 先判断命令属于 Agent 语义、确定性应用服务、Provider adapter 还是维护工具。
2. 在 `lib/command_registry.py` 增加唯一 `CommandSpec`，声明执行入口、使用者、可见性、
   stability、副作用、协议、runtime 和文档。
3. Agent 可调用的确定性 Python 工具应接入 `byteworker-cli.py` 的稳定 envelope；不得在 facade
   或 launcher 中按工具名新增平行分支。
4. 新增、删除或重命名命令时，由 registry 驱动帮助/manifest/测试；逻辑路径变化用
   `CommandAlias` 指向唯一执行入口，不复制 handler。本文件只更新 narrative，不再维护命令名单。
5. 信息流、模块职责或跨层契约变化时，同步 `docs/development/ARCHITECTURE.md`。
6. schema 或知识库目录变化时，同步 `docs/development/DESIGN.md`。
7. Agent 行为变化时，同步 `SKILL.md` 和对应 `references/`。
8. 更新或新增命令测试，并运行：

```bash
python3 -m compileall -q bin lib tests
for script in bin/*.sh; do bash -n "$script"; done
python3 -m coverage erase
python3 -m coverage run -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage combine
python3 -m coverage report
git diff --check
```

完整测试还需要 Node.js 22（viewer runtime）。覆盖率规则集中在
根目录 `.coveragerc`，CI 与本地使用同一套 branch coverage 门禁。
