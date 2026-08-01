# byteworker · 确定性 CLI 公共协议

> 所有 Agent/自动化调用只需加载这份公共 envelope。具体工具参数运行
> `bin/byteworker <tool> --help` 或读取对应 workflow reference，不在这里集中复制。

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
- 人工阅读可把全局 `--pretty` 放在 tool 前。

可用工具以 `bin/byteworker --help` 为准，当前包括：
`digest-txn`、`kb-mutate`、`kb-query`、`context`、`doctor`、`todo`、`source`、`wiki`、
`digest-job`、`report-automation`、`provenance-backfill`、`index`、`update-status`。

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

## 写入成功判定

- digest：只认 `digest-txn execute` 的 `status=committed` receipt。
- 非 digest：只认 `kb-mutate execute` 的 `status=committed` receipt。
- Todo：只认 todo 工具返回的新状态。
- 自动报告：报告 mutation committed 后，再以真实结果调用 `report-automation complete`。

直接 `bin/*.py` 入口仅供人工排障和旧调用方。Agent 不手工补做工具已负责的 hash、INDEX、
journal、暂存、commit 或 rollback。
