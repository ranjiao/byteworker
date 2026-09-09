# byteworker · 大输出协议

`commands list --json` 的 `output_policy` 是当前事实。普通结果内联在 `byteworker-cli/v1.data`；
stdout 越过 `inline_stdout_limit_bytes` 时，`data` 返回 `byteworker-cli-artifact/v1` receipt。失败时
receipt 位于 `error.details.output_artifact`。

artifact 位于系统临时目录、权限为 `0600`，receipt 包含路径、字节数、SHA-256、content type 和
`temporary=true`。调用方必须先校验 hash，读取后删除。命令支持 `--out` 时优先显式指定受控路径。
