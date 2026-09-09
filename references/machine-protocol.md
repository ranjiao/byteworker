# byteworker · 确定性 CLI 公共协议

> Agent/自动化只加载公共 envelope；参数通过
> `bin/byteworker <tool> --help`、`commands describe` 或对应 workflow reference 按需读取。

## Session 与 runtime

每个新 session 先运行一次 `bin/byteworker preflight`。健康时无输出；有输出时解析
`byteworker-session-preflight/v1.notices`。launcher 负责 Python >=3.10 和一致的运行环境：

- 工具：`bin/byteworker <tool> ...`
- 飞书 CLI：`bin/byteworker lark ...`
- 继承同一环境的脚本：`bin/byteworker run <command> ...`

不要直接猜 Python、nvm、venv、lark-cli 或 meegle 路径。

## Envelope

facade 输出一行 `byteworker-cli/v1` JSON：

```json
{"status":"success","data":{},"error":null,"context":{"protocol":"byteworker-cli/v1","tool":"kb-query","operation":"search","execution_time_ms":12}}
```

- `success`：业务结果在 `data`。
- `attention`：命令完成但有 finding/待处理状态。
- `error`：读取 `error.code/message/hint/details`。
- 同时检查退出码；不要只看 stdout 是否非空。
- `context` 不回显业务正文或完整 argv。
- `context.command_path/stability/side_effect` 来自统一命令 registry。
- 人工阅读可把全局 `--pretty` 放在 tool 前。

命令清单按需读取：

```bash
bin/byteworker commands list --json
bin/byteworker commands describe source.capture --json
```

协议为 `byteworker-command-manifest/v1` 和 `byteworker-command-description/v1`；
任意层级 `--help` 都是无 runtime/state 副作用的普通文本。

## SourceBundle request 快速参考

`source bundle --request` 接受系统临时目录或 KB 内的 JSON **文件路径**，不接受内联 JSON。
拿不准字段时先运行：

```bash
bin/byteworker source bundle-spec --source-type "<source_type>"
```

它返回真实 `required_fields/optional_fields`、artifact 字段、source UID 规则、component 契约和
最小示例。`body/transcript/local_file/comments/whiteboards[]` 等 component 至少提供绝对
`path`；不要猜 `content` 或 `content_path`。业务 artifact、request、plan 和候选不得放进
skill 仓库。

`source auth-status` 是无副作用状态检查。`ready=false` 仍可能是成功 envelope；真正的
inspect/capture 会以稳定错误码 fail closed。

外部凭据不支持 `auth status` 时，飞书群聊只用 `whoami --as user` 检查 user；scope 留到
capture-time，失败只修宿主注入，不本地登录或切 bot。

## 写入成功判定

- digest：候选完成后直接调用 `digest-txn execute`；它内含完整 validate 与锁内复验。只认
  `status=committed` receipt；独立 `validate` 仅用于失败诊断。
- 非 digest：只认 `kb-mutate execute` 的 `status=committed` receipt。
- Todo：只认 todo 工具返回的新状态。
- 自动报告：报告 mutation committed 后，再以真实结果调用 `report-automation complete`。
- Dreaming：默认关闭；只有用户确认机器运行要求后才调用 `dreaming enable`。每个宿主 tick
  通过 `run-due` 领取至多一个 job，并在所有路径调用 `complete`。

直接 `bin/*.py` 入口仅供人工排障和旧调用方。Agent 不手工补做工具已负责的 hash、INDEX、
journal、暂存、commit 或 rollback。
