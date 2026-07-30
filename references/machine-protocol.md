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
python3 bin/byteworker-cli.py kb-query source-record --kb "<知识库目录>" \
  --source-type meego --record-id "<work_item_id>"
python3 bin/byteworker-cli.py kb-query source-record --kb "<知识库目录>" \
  --source-type feishu_base --title "<记录标题>" --limit 5
python3 bin/byteworker-cli.py kb-query source-record --kb "<知识库目录>" \
  --source-type aeolus --record-id "report:<report_id>"

# doctor、Todo、provenance 回填
python3 bin/byteworker-cli.py doctor scan --kb "<知识库目录>"
python3 bin/byteworker-cli.py todo "<知识库目录>" check
python3 bin/byteworker-cli.py provenance-backfill audit --kb "<知识库目录>"

# 自动更新状态；只读，不触发网络检查
python3 bin/byteworker-cli.py update-status

# 统一来源能力、Profile、capture 与 Bundle
python3 bin/byteworker-cli.py source capabilities
python3 bin/byteworker-cli.py source auth-status --source-type meego \
  --host project.feishu.cn
python3 bin/byteworker-cli.py source auth-status --source-type feishu_base
python3 bin/byteworker-cli.py source inspect --source-type meego --url "<Meego 视图 URL>"
python3 bin/byteworker-cli.py source inspect --source-type meego \
  --url "<Meego 空间主页 URL>"
python3 bin/byteworker-cli.py source capture --source-type meego --url "<Meego 视图 URL>" \
  --field name --field status --out "<临时目录>/meego-capture.json" \
  --bundle-out "<临时目录>/meego-bundle.json"
python3 bin/byteworker-cli.py source profile-save --kb "<知识库目录>" \
  --file "<临时目录>/meego-profile.json"
python3 bin/byteworker-cli.py source capture --source-type meego \
  --kb "<知识库目录>" --source-uid "meego:<project_key>:<view_id>" \
  --out "<临时目录>/meego-capture.json"
python3 bin/byteworker-cli.py source diff --kb "<知识库目录>" \
  --source-uid "meego:<project_key>:<view_id>" \
  --current "<本次 capture.json>" --out "<临时目录>/meego-diff.json"
python3 bin/byteworker-cli.py source inspect --source-type feishu_base --url "<Base 视图 URL>"
python3 bin/byteworker-cli.py source capture --source-type feishu_base --url "<Base 视图 URL>" \
  --field fld_title --field fld_status --out "<临时目录>/base-capture.json" \
  --bundle-out "<临时目录>/base-bundle.json"
python3 bin/byteworker-cli.py source auth-status --source-type aeolus
python3 bin/byteworker-cli.py source inspect --source-type aeolus --url "<风神 dashboard URL>"
python3 bin/byteworker-cli.py source register --source-type aeolus \
  --kb "<知识库目录>" --url "<风神 dashboard URL>" \
  --report-id "<report_id>" --filter-mode dashboard --routine weekly
python3 bin/byteworker-cli.py source profiles --kb "<知识库目录>" --source-type aeolus
python3 bin/byteworker-cli.py source capture --source-type aeolus \
  --kb "<知识库目录>" \
  --source-uid "aeolus:<region>:<app_id>:<dashboard_id>:<sheet_id>" \
  --out "<临时目录>/aeolus-capture.json" \
  --bundle-out "<临时目录>/aeolus-bundle.json"
python3 bin/byteworker-cli.py source capture --source-type feishu_chat \
  --kb "<知识库目录>" --source-uid "<chat_id>" \
  --out "<临时目录>/chat-bundle.json"
python3 bin/byteworker-cli.py source bundle --source-type web \
  --request "<临时目录>/web-bundle-request.json" \
  --out "<临时目录>/web-bundle.json"

# 按需 Wiki 空间探索（普通 Wiki 文档仍走 feishu_doc）
python3 bin/byteworker-cli.py wiki auth-status
python3 bin/byteworker-cli.py wiki inspect --url "<Wiki URL>"
python3 bin/byteworker-cli.py wiki scan --kb "<知识库目录>" --url "<Wiki URL>"
python3 bin/byteworker-cli.py wiki scan --kb "<知识库目录>" \
  --source-uid "feishu_wiki:<space_id>:<root_node_token>"
python3 bin/byteworker-cli.py wiki topics --kb "<知识库目录>" \
  --space-id "<space_id>" --limit 30
python3 bin/byteworker-cli.py wiki candidates --kb "<知识库目录>" \
  --space-id "<space_id>" --root-node-token "<node_token>" \
  --out "<临时目录>/wiki-selection.json"

# 已确认页面的可恢复批量任务
python3 bin/byteworker-cli.py digest-job create --kb "<知识库目录>" \
  --selection "<临时目录>/wiki-selection.json" --batch-size 5
python3 bin/byteworker-cli.py digest-job list --kb "<知识库目录>" --active
python3 bin/byteworker-cli.py digest-job next --kb "<知识库目录>" \
  --job-id "<job_id>" --limit 5 --lease-owner "<session_id>"
python3 bin/byteworker-cli.py digest-job mark --kb "<知识库目录>" \
  --job-id "<job_id>" --document-id "<document_id>" \
  --status committed --raw-id "<raw_id>" --commit "<commit>"

# 人工可读输出
python3 bin/byteworker-cli.py --pretty doctor scan --kb "<知识库目录>"
```

`source auth-status` 只读且不发起 OAuth。它返回
`authenticated / authorized / ready / missing_scopes / action`；未登录或缺 scope 仍是
`status=success,data.ready=false`,因为“状态检查成功但来源尚未就绪”不是传输错误。
真正的 `inspect / capture` 会 fail closed 为 `SOURCE_AUTH_REQUIRED`，并把同一
`auth_action` 放进 error details。

`source capabilities` 分别列出 operation、Profile 与 Bundle 来源集合；三者是正交能力，
不把“已有 Bundle adapter”误报成“也有同形网络 capture”。`source inspect` 只读返回真实
字段/报表元数据与规模；Meego 空间主页在 URL decode 后返回
`SOURCE_SELECTION_REQUIRED`，提示提供具体 Story View URL，不发起候选搜索、页面自动化或
工作项读取。`source profile-save` 严格校验
`byteworker-source-profile/v2`（当前 Meego/Base/群聊/飞书文档/Wiki 子树）并事务写入用户 KB；风神
`source register` 继续写其兼容 v1 profile。后续按 `source_uid` capture，且不接受 CLI
覆盖该 profile。Meego/Base/Aeolus `source capture --bundle-out` 在完整读取后同时写规范
快照和 Bundle；群聊 Profile capture 直接输出 Bundle。飞书文档、妙记、Web、本地文件由宿主
先抓取 artifact，再用 `source bundle --request` 规范化。`source diff --kb` 通过
SnapshotStore 从已提交 raw 选择上一份完整快照，也可用 `--raw-id` 或 `--history-index`
显式选择历史版本。diff 按稳定记录 ID 比较，输出
`baseline / added / changed / left_view`。
其中 `left_view` 明确不代表来源记录已删除，diff 是可重算派生物，不替代 raw 中的完整快照。

`wiki` 和 `digest-job` 是独立的惰性应用服务：普通命令不会导入其模块、检查授权、扫描状态或
创建目录。Wiki 完整树只写 `<KB>/state/wiki/`，facade 只返回有限摘要；任务只在用户确认页面后
创建于 `<KB>/state/digest_jobs/`，通过小批租约和逐页 receipt 标记跨 session 恢复。树探索不生成
SourceBundle；被选页面仍逐个走 `feishu_doc` 标准事务。

`kb-query source-record` 只读本地 `raw_data/`：先轻量扫描 frontmatter，再默认选择每个
`source_uid` 的最新完整 Meego / Base / 风神快照并解析 JSON。`--record-id` 是稳定 ID 精确匹配；
`--title` 在 Python 内执行 Unicode、大小写、空白和标点归一化，并结合包含、分词覆盖与字符
相似度排序；可用 `--title-threshold 0..1` 调整阈值。多条命中时 `ambiguous=true`，每条返回
`match.kind / score / field`、完整单条 `record` 及
`raw_id / raw_path / ingested / source_url / anchor_id / anchor / is_latest_snapshot`。默认不搜索旧快照；
用户明确需要历史记录时加 `--history`。调用方不得把大 raw 的 `rg` / `grep` 输出当作替代品。

若底层参数需要与 facade 自身参数隔离，可在 tool 后加 `--`；例如
`python3 bin/byteworker-cli.py doctor -- scan --kb "<知识库目录>"`。

## 兼容边界

- `bin/digest-txn.py`、`bin/kb-query.py`、`bin/doctor.py`、`bin/todo.py`、
  `bin/provenance-backfill.py` 的参数和原始输出保持不变；`bin/source.py` 新增
  `capabilities`、`bundle` 与兼容的 `--bundle-out`，旧 capture 参数保持有效。
- Agent 与新自动化优先使用 facade；已有人工命令或外部脚本可渐进迁移。
- facade 只适配确定性本地 CLI，不做语义判断，不接入注册表，也不改变知识库写入授权。
- `data` 保留底层 JSON 结构；底层若输出纯文本，则 `data` 是字符串，不臆造字段。
