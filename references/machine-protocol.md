# byteworker · 确定性 CLI 机器协议

> 由 `SKILL.md` 的「机器协议」路由到这里。面向 Agent、自动化脚本和宿主集成；人工排障仍可
> 直接运行原有 CLI。

## 为什么需要统一入口

历史 CLI 的输出结构和失败表达各不相同，调用方需要记住每个脚本的特殊分支。统一入口
`bin/byteworker-cli.py` 不改变底层业务逻辑，只把确定性命令适配成稳定的
`byteworker-cli/v1` JSON envelope：

```json
{"status":"success","data":{},"error":null,"context":{"protocol":"byteworker-cli/v1","tool":"doctor","operation":"scan","execution_time_ms":12}}
```

- `status=success`：命令成功，业务结果在 `data`。
- `status=attention`：命令正常完成，但需要关注；当前用于 doctor 扫描仍有 finding 的退出码 2。
- `status=error`：命令失败，`error.code/message/hint/details` 给出稳定错误信息。
- `context` 只含协议版本、工具、操作和耗时，不回显完整 argv、查询或业务正文。

默认输出严格为单行 JSON，便于日志和流式调用；人工阅读时把全局 `--pretty` 放在 tool 前。
进程退出码仍保留底层 CLI 的语义，调用方应同时检查退出码与 envelope。

## 调用方式

```bash
# digest 事务
python3 bin/byteworker-cli.py digest-txn preflight --kb "<知识库目录>" --source "<source.json>"
python3 bin/byteworker-cli.py digest-txn validate --kb "<知识库目录>" --plan "<plan.json>"
python3 bin/byteworker-cli.py digest-txn execute --kb "<知识库目录>" --plan "<plan.json>"

# 查询与证据解析
python3 bin/byteworker-cli.py kb-query search --kb "<知识库目录>" --query "<查询>"
python3 bin/byteworker-cli.py kb-query evidence --kb "<知识库目录>" --node "<node-id>" --markers E1,E2

# doctor、Todo、provenance 回填
python3 bin/byteworker-cli.py doctor scan --kb "<知识库目录>"
python3 bin/byteworker-cli.py todo "<知识库目录>" check
python3 bin/byteworker-cli.py provenance-backfill audit --kb "<知识库目录>"

# 自动更新状态；只读，不触发网络检查
python3 bin/byteworker-cli.py update-status

# 人工可读输出
python3 bin/byteworker-cli.py --pretty doctor scan --kb "<知识库目录>"
```

若底层参数需要与 facade 自身参数隔离，可在 tool 后加 `--`；例如
`python3 bin/byteworker-cli.py doctor -- scan --kb "<知识库目录>"`。

## 兼容边界

- `bin/digest-txn.py`、`bin/kb-query.py`、`bin/doctor.py`、`bin/todo.py`、
  `bin/provenance-backfill.py` 的参数和原始输出保持不变。
- Agent 与新自动化优先使用 facade；已有人工命令或外部脚本可渐进迁移。
- facade 只适配确定性本地 CLI，不做语义判断，不接入注册表，也不改变知识库写入授权。
- `data` 保留底层 JSON 结构；底层若输出纯文本，则 `data` 是字符串，不臆造字段。
