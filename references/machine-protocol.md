# byteworker · 确定性 CLI 公共协议

> Agent/自动化只加载公共 envelope；参数通过
> `bin/byteworker <tool> --help`、`commands describe` 或对应 workflow reference 按需读取。

## Session 与 runtime

每个新 session 先按 `SKILL.md` 运行一次 preflight。定时任务或 runner 使用
`bin/byteworker preflight --unattended`。无参数按无人值守安全默认处理。
健康时无输出；有输出时解析 `byteworker-session-preflight/v1.notices`。launcher 负责 Python >=3.10
和一致的运行环境：

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

`--help` 返回只读文本。`describe` 返回实际参数、子命令及 `source` 的分来源参数。

返回 `byteworker-cli-artifact/v1` 时按 receipt 的 SHA-256 校验临时文件，读取后删除；
失败的大输出见 `error.details.output_artifact`。完整字段见 `command-output.md`。

## 写入成功判定

- 普通 digest：候选完成后调用 `digest-flow commit`，以内部 `receipt.status` 确认结果。会议簇
  批量事务按 `references/digest-meeting.md` 调用 `digest-txn execute`。
- 非 digest：只认 `kb-mutate execute` 的 `status=committed` receipt。
- 自动报告：报告 mutation committed 后，再以真实结果调用 `report-automation complete`。
- Dreaming：默认关闭。经用户确认后启用。宿主从 `dreaming tick` 开始。报告成功用
  `dreaming report complete`，其它终态按 `templates/dreaming-runner.md` 收尾。

直接 `bin/*.py` 入口仅供人工排障和旧调用方。Agent 不手工补做工具已负责的 hash、INDEX、
journal、暂存、commit 或 rollback。
