# 命令性能与输出边界基线

> 生命周期：`point_in_time`。本文是时间点证据，不是跨机器性能承诺或当前规范。
>
> 测量日期：2026-09-09。该记录用于 CR-10 的架构决策，不是跨机器性能承诺。

## 结论

- 保留 shell bootstrap、launcher、facade 和直接工具之间的进程隔离；当前没有证据支持单进程 fast path。
- facade 的 stdout 改为有界流式收集：不超过 1 MiB 时保持原有 inline JSON，超过后写入系统临时
  目录的 `0600` artifact，并只在 envelope 中返回 receipt。
- stderr 持续排空但最多保留 64 KiB，后续错误协议仍只展示既有的 2048 字符摘要。
- 性能回归不设置易受机器负载影响的 CI 墙钟阈值；以协议、内存边界和 artifact 权限测试作为稳定门禁。

## 本地微基准

暖缓存后每项执行 20 次，p95 取排序后的第 19 个样本。命令输出由外层测量进程收集，不包含网络、
Git 或模型调用。

| 路径 | 改造前 p50 / p95 | 有界流式输出后 p50 / p95 | stdout |
|---|---:|---:|---:|
| `python3 bin/source.py capabilities` | 107.9 / 121.6 ms | 未修改 | 5004 B |
| `python3 bin/byteworker-cli.py source capabilities` | 141.7 / 152.3 ms | 144.1 / 155.4 ms | 5275 B |
| `bin/byteworker source capabilities` | 238.2 / 247.7 ms | 244.8 / 257.0 ms | 5275 B |
| `bin/byteworker commands list --json` | 64.1 / 68.8 ms | 66.3 / 74.5 ms | 21544 B |

完整 launcher 相对直接工具的固定包装成本约 130 ms。有界收集对普通小输出的 p50 增量约
2 至 7 ms，没有把磁盘创建带入健康路径；只有输出真正超过阈值时才创建 artifact。

## Workflow 样本

只读取已配置知识库中的隐私安全 digest timing metadata，不读取或记录正文、标题、人员、URL 或
source ref。样本包含最近 40 个 run，其中 7 个为已终止状态：

| 指标 | p50 | p95 | 最大值 |
|---|---:|---:|---:|
| active duration | 136860 ms | 296851 ms | 未用于决策 |
| lifecycle/event 数 | 4 | 6 | 6 |
| 单次 `show` JSON | 3103 B | 4023 B | 4219 B |

`list --limit 50` 的实测 envelope 为 83804 B。按每个 timing event 都由独立 CLI 调用这一偏保守假设，
4 至 6 次调用的固定包装约为 0.52 至 0.78 秒，在已终止样本中通常低于 active duration 的 1%。
样本量较小，且不能代表所有 provider；如果未来 workflow 观测显示本地轻命令达到几十次并占总耗时
显著比例，应优先把调用合并到 application service，再重新评估 fast path。

## 输出协议

超过 1 MiB 的成功 stdout 不再内联解析，`byteworker-cli/v1.data` 返回：

```json
{
  "protocol": "byteworker-cli-artifact/v1",
  "artifact_path": "/private/tmp/byteworker-cli-output-....out",
  "bytes": 1234567,
  "sha256": "sha256:<hex>",
  "content_type": "application/json",
  "mode": "0600",
  "temporary": true
}
```

失败或 attention 的大 stdout 使用同一 receipt；失败时 receipt 位于
`error.details.output_artifact`。调用方读取后负责删除临时 artifact。阈值是 envelope 承载边界，
不是底层命令业务数据上限；已支持显式 `--out` 的命令仍应优先写其受控 artifact。
