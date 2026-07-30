# byteworker · digest 细则 —— Meego 保存视图

> 由 `SKILL.md` / `references/digest-core.md` 在 `source_type=meego` 时路由到这里。
> 只读保存视图，不执行 MQL、不展开详情、评论、附件或关联工作项。大看板使用
> “全量快照 → 逐工作项差异 → 知识晋升门槛”，不把每条需求做成节点。

## 1. 范围与前置

- 用户传入 `project_home` / `project_overview` 空间级 URL 时，不直接 digest、不遍历空间工作项，
  也不调用 `project search` / `view search` 或页面自动化尝试发现视图。URL 解析确认是空间主页后，
  立即返回 `SOURCE_SELECTION_REQUIRED`，提醒用户提供包含 `/storyView/<view_id>` 的具体
  Story View 页面 URL。
- 空间主页本身不作为来源，不创建 Profile、不写 raw，也不触发 OAuth。只有收到明确 Story View
  URL 后才运行标准 `source inspect`，再进入下面的保存视图流程。
- 仅接受 `meegle url decode` 识别为 `view_story / view_issue / view_workitem` 且返回稳定
  `view_id` 的项目内保存视图 URL；禁止手拆 URL。跨项目视图、甘特、图表暂不纳入第一版。
- 先用机器协议运行 `source inspect`。它执行 Meego Auth Guard、把 `simple_name` 转为权威
  `project_key`，并只读取视图首批来验证权限和范围。
- Meego 使用独立于 `lark-cli` 的 OAuth。未登录时 `SOURCE_AUTH_REQUIRED` 会给出与 URL host
  一致的 `meegle auth login --host <host>`；取得用户同意后才运行。登录成功但空间
  Permission Denied 时让空间所有者给当前用户开权限，重复登录无效。
- `inspect` 返回权威字段元数据、首批数量与总量估计。选择最小稳定投影：名称、状态、负责人、
  优先级、分类、排期/更新时间；用户指定字段时以用户要求为准。首次摄取需向用户说明字段投影，
  并保存为 `byteworker-source-profile/v2` 的 `capture_policy.fields`；后续 routine 沿用，
  不随意增删列。
- 首次 capture 至少传一个 `--field`，默认上限 1000 条；超出上限先说明规模并让用户确认是否
  缩小视图或显式提高 `--max-items`。权威 project/view 坐标和确认后的字段随后写入 profile；
  以后只按 `--kb + --source-uid` 抓取，不接受同次 CLI 覆盖。
- 默认只读当前视图返回的字段。不递归读取工作项详情、评论、附件、群聊、关联工作项或正文链接。

```bash
bin/byteworker source auth-status \
  --source-type meego --host project.feishu.cn

bin/byteworker source inspect \
  --source-type meego --url "<Meego 保存视图 URL>"

# 空间主页：返回 SOURCE_SELECTION_REQUIRED，提示提供具体 Story View URL
bin/byteworker source inspect \
  --source-type meego --url "<Meego 空间主页 URL>"

bin/byteworker source capture \
  --source-type meego --url "<Meego 保存视图 URL>" \
  --field name --field status --field owner --field updated_at \
  --out "<系统临时目录>/meego-capture.json" \
  --bundle-out "<系统临时目录>/meego-bundle.json"

# 首次确认后，在系统临时目录准备 byteworker-source-profile/v2 并保存
bin/byteworker source profile-save \
  --kb "<知识库目录>" \
  --file "<系统临时目录>/meego-profile.json"

# 后续按稳定 source UID 重放 profile
bin/byteworker source capture \
  --source-type meego --kb "<知识库目录>" \
  --source-uid "meego:<project_key>:<view_id>" \
  --out "<系统临时目录>/meego-capture.json" \
  --bundle-out "<系统临时目录>/meego-bundle.json"

bin/byteworker source diff \
  --kb "<知识库目录>" \
  --source-uid "meego:<project_key>:<view_id>" \
  --current "<本次完整 capture.json>" \
  --out "<系统临时目录>/meego-diff.json"
```

## 2. 完整性与快照

- `capture` 通过官方 `meegle --auto-paginate --envelope view get` 读取；只接受
  `pagination.complete=true`。CLI 返回 `truncated=true` 或仍有 `has_more=true` 时中止，
  不得把部分结果交给 digest。
- 记录必须有稳定 `work_item_id`，按 ID 排序。`captured_at` 在 hash 外；
  `snapshot` 使用 canonical JSON 计算 `content_hash`，API 返回顺序变化不产生新版本。
- 工作项字段里的 URL 在进入 snapshot/hash 前统一剥离一次性登录 token、access token、
  authorization、签名等敏感 query 参数；只保留脱敏计数，不保留凭据值。
- `source_uid` 固定为 `meego:<project_key>:<view_id>`。不写 `digest_period` /
  `source_window`：同一视图内容不变时事务 `noop`；增删改导致 hash 变化时是同源新版本。
- 每个工作项生成 `kind=meego_workitem` 的 exact anchor，locator 至少含
  `project_key + view_id + work_item_id`。没有稳定 ID 时整次失败。

## 3. 相邻快照差异

- 首次没有上一份快照时，`source diff` 把所有记录标为 `baseline`，表示建立基线，不等于
  同数量的新增需求。
- 后续按稳定 `work_item_id` 分类为 `added / changed / left_view`；未变记录只计数、不重复交给
  语义 digest。`changed_paths` 用于缩小复核范围。
- `left_view` 只表示离开这个保存视图，禁止写成“已删除 / 已取消”。若要确认真实终态，需另行
  读取权威工作项。
- diff JSON 位于系统临时目录，是相邻完整快照的可重算派生物；raw 保存当前完整 snapshot，
  不能只存 diff。

## 4. 交给 digest 事务

Meego adapter 把 capture 转成 `byteworker-source-bundle/v2`。capture 文件的 `/snapshot`
作为 canonical component，来源身份、coverage、anchors、profile path/revision 与 canonical
`record_index` 同时进入 bundle。结构示意：

```json
{
  "schema_version": "byteworker-source-bundle/v2",
  "identity": {
    "source_type": "meego",
    "source_uid": "meego:<project_key>:<view_id>",
    "source_url": "<原保存视图 URL>",
    "title": "<视图标题>"
  },
  "components": [{
    "name": "snapshot",
    "kind": "records",
    "path": "<系统临时目录>/meego-capture.json",
    "json_pointer": "/snapshot",
    "mode": "canonical-json",
    "heading": "Meego 视图快照"
  }],
  "coverage": {
    "status": "complete",
    "components": {"snapshot": "complete"}
  },
  "anchors": "<capture.anchors + source anchor>",
  "provider_metadata": {
    "project_key": "<project_key>",
    "view_id": "<view_id>",
    "fields": ["name", "status", "owner", "updated_at"],
    "source_profile": {
      "path": "sources/meego-<hash>.json",
      "revision": "sha256:<canonical profile hash>"
    }
  },
  "record_index": "<adapter 生成的稳定记录索引>"
}
```

然后创建 `digest-plan/v2`，只引用 bundle；plan 不再复制 `source` 或 anchors。
首次 digest 创建一张代表该视图的 `reading` 主记录；同源新快照更新同一 reading。对 baseline /
added / changed 逐条应用晋升门槛:

- 普通需求、状态变化、负责人/排期变化 → 仅 raw + provenance；
- 长期持续、需跨文档/会议追踪的工作 → 创建/更新 `project`；
- 明确生效的取舍、原则、边界 → `decision`；
- 评审、发布、事故等有时间语义的事实 → `event`；
- 多条需求稳定复现的能力方向或风险模式 → `area`。

不要为每条需求创建节点，也不要仅因工作项出现某个 owner 就创建 person。Meego 是状态、负责人、
优先级和排期的权威来源；文档/会议/群聊是理由、讨论过程和决策的权威来源。

## 5. 定期摄取

用户确认纳入 routine 后，把 `enabled/cadence` 写入该 Meego profile，raw 只记录本次使用的
profile path/revision，不再充当下一次调度配置。后续加载 profile 重新 capture 完整视图并走
`digest-txn preflight`。完整快照 hash 相同只记录“已复查、无变化”；不同时让 SnapshotStore
从 KB 选择上一份已提交完整 raw，再运行 `source diff --kb ... --source-uid ...`，只把差异记录
交给语义层，raw 仍保存本次全量快照。只有事务 receipt `status=committed` 才表示知识库更新成功。
