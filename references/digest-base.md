# byteworker · digest 细则 —— 飞书多维表格视图

> 由 `SKILL.md` / `references/digest-core.md` 在 `source_type=feishu_base` 时路由到这里。
> 只读一个明确 Base 视图，不修改表、字段、记录或视图配置。大表采用
> “全量快照 → 逐记录差异 → 知识晋升门槛”，不把每行做成节点。

## 1. 范围与前置

- 用户给 URL 时先通过机器协议运行 `source inspect`；底层必须使用
  `bin/byteworker lark base +url-resolve --as user`，不得把 wiki token 或完整 URL 猜成 base token。
- Base 复用 `lark-cli` 用户登录，但摄取前还必须具备
  `base:app:read / base:table:read / base:field:read / base:view:read / base:record:read`。
  `source auth-status` 会一次列全缺失 scope 和 split-flow 命令；取得用户同意后再发起 OAuth，
  展示原样 verification URL + 二维码，用户完成后由 agent 用 device code 收尾。
- scope 齐全后若返回 `91403`，这是具体 Base / 表 / 视图的资源权限；请所有者给当前用户共享，
  不要重复登录，也不要自动降级 bot。
- 第一版要求明确 `base_token + table_id + view_id`。inspect 串行读取 Base、表、视图和全部字段
  metadata，返回真实字段 ID / 名称；让用户确认最小字段投影后再 capture。
- `capture` 至少传一个 `--field`，可用 field ID 或精确名称。默认上限 1000 条；超出上限先说明
  规模并让用户确认缩小视图或显式提高 `--max-items`。
- 默认不下载附件，不展开关联表，不读取单记录历史，不做写回。公式 / lookup 只保留当前返回值和
  field schema，不能把计算结果反推成来源事实。

```bash
bin/byteworker source auth-status \
  --source-type feishu_base

bin/byteworker source inspect \
  --source-type feishu_base --url "<Base 视图 URL>"

bin/byteworker source capture \
  --source-type feishu_base --url "<Base 视图 URL>" \
  --field fld_title --field fld_status --field fld_owner --field fld_updated \
  --out "<系统临时目录>/base-capture.json" \
  --bundle-out "<系统临时目录>/base-bundle.json"

bin/byteworker source diff \
  --previous "<上一份完整 capture.json>" \
  --current "<本次完整 capture.json>" \
  --out "<系统临时目录>/base-diff.json"
```

## 2. 完整性与快照

- `capture` 用 `+record-list --view-id --limit 200 --offset N` 串行翻页；严禁并发。
  `has_more=true`、总数未满足或满页时必须继续；空页但仍有下一页、offset 不推进、记录 ID 重复、
  超过 200 页时中止，不生成快照。
- 记录必须有稳定 `record_id`，按 ID 排序；字段 schema 按 field ID 排序。
  `captured_at` 在 hash 外，`snapshot` 使用 canonical JSON 计算 `content_hash`。
- 记录字段里的 URL 在进入 snapshot/hash 前统一剥离一次性登录 token、access token、
  authorization、签名等敏感 query 参数；只保留脱敏计数，不保留凭据值。
- `source_uid` 固定为
  `feishu_base:<base_token>:<table_id>:<view_id>`。完整快照能发现记录离开当前视图；
  `source diff` 将其标为 `left_view`，不直接推断记录已删除。第一版不使用 `updated_at` 增量游标。
- 每条记录生成 `kind=base_record` 的 exact anchor，locator 至少含
  `base_token + table_id + view_id + record_id`。
- 首次确认 selector/fields/page_size/max_records 后保存 v2 Profile；routine 必须按
  `source_uid` 重放 Profile，不得从最近 raw 拼接 Base 坐标。

本命令解决的是“完整、可重复的原始记录快照”，不是 Base 统计分析入口。需要 TopN、全局聚合或
跨表结论时，按 `lark-base` skill 改走 Base 云端 filter/sort/`+data-query`，不要从 digest
快照临时手算后宣称是权威统计。

## 3. 相邻快照差异

- 首次无上一份快照时全部记录标为 `baseline`，只表示建立基线。
- 后续按稳定 `record_id` 生成 `added / changed / left_view`；未变记录只计数。
- diff 是系统临时目录中的可重算派生物，只用于缩小语义复核范围；raw 始终保留当前完整
  snapshot。`left_view` 不等于删除，尤其当视图过滤条件会随状态变化时。

## 4. 交给 digest 事务

```json
{
  "type": "feishu_base",
  "uid": "feishu_base:<base_token>:<table_id>:<view_id>",
  "url": "<原 Base 视图 URL>",
  "title": "<Base / 表 / 视图标题>",
  "base_token": "<base_token>",
  "table_id": "<table_id>",
  "view_id": "<view_id>",
  "fields": ["fld_title", "fld_status", "fld_owner", "fld_updated"],
  "components": [{
    "name": "snapshot",
    "kind": "records",
    "path": "<系统临时目录>/base-capture.json",
    "json_pointer": "/snapshot",
    "mode": "canonical-json",
    "heading": "多维表格视图快照"
  }]
}
```

capture adapter 把 `anchors[]` 和 source anchor 写入 SourceBundle；`digest-plan/v2` 只引用该
Bundle，顶层 provenance 只允许 `enrichment`，不得复制 anchors。
首次 digest 创建代表该视图的 `reading` 主记录；同源新快照更新同一 reading。普通记录变化只留
raw + provenance；只有长期项目、明确生效决策、时间事件或跨记录稳定主题达到晋升门槛时才更新
`project / decision / event / area`，不要一行一个节点或因负责人列自动创建 person。

## 5. 定期摄取

用户确认纳入 routine 后，保存带 cadence 的 v2 Profile；它是 selector、fields、page size、
max records 和 routine 状态的唯一运行配置。后续按 `source_uid` 原样重放 Profile，重新 capture
完整视图并走 `digest-txn preflight`，不得从最近 raw 恢复或拼接 Base 坐标与字段。raw 只记录
本次实际参数、Profile path/revision 和 capture 结果，作为历史执行证据，不是下一次配置真相源。
完全相同则只记录“已复查、无变化”；不同则运行 `source diff`，只把差异记录交给语义层，raw
仍保存完整快照。任何权限或分页不完整错误都不得写 raw，也不得把该来源标成复查成功。
