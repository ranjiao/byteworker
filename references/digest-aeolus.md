# byteworker · digest 细则 —— 风神看板

> 由 `SKILL.md` / `references/digest-core.md` 在 `source_type=aeolus` 时路由到这里。
> 一个 dashboard sheet 是 KB 里的一个独立来源，和一份飞书文档同等粒度。skill 只提供
> 通用查询逻辑；dashboard、sheet、report selector、filter 和 cadence 全部属于该来源自己的
> KB profile，禁止写死在 skill 或留给 Agent 临场拼接。

## 1. 来源模型与前置

- 仅接受风神 dashboard URL。稳定来源 ID 是
  `aeolus:<region>:<app_id>:<dashboard_id>:<sheet_id>`；两个不同 sheet 必须注册为两个 profile。
- profile 存在用户 KB 的 `sources/`，使用 `byteworker-source-profile/v1`，不含凭据和数据行。
  凭据只来自环境变量或仓库外 `0600` 文件。
- 先运行 `source auth-status --source-type aeolus`。`ready=false` 时只提示配置现有凭据；
  routine 不自动登录。
- `source inspect` 是只读预览；`source register` 才把用户确认的选择器和筛选口径写入 KB，
  同时重建 INDEX、追加 journal 并创建 KB 本地 Git 回滚点。
- 首次 digest 必须先 register；之后一律按 `--source-uid` 抓取。profile 抓取不接受 CLI
  覆盖 URL、report、filter 或行数。改变口径要显式重新 register，得到新的 profile revision。

```bash
python3 bin/byteworker-cli.py source auth-status \
  --source-type aeolus

python3 bin/byteworker-cli.py source inspect \
  --source-type aeolus --url "<风神 dashboard URL>"

python3 bin/byteworker-cli.py source register \
  --source-type aeolus --kb "<知识库目录>" \
  --url "<风神 dashboard URL>" \
  --report-id "<report_id>" \
  --filter-mode explicit \
  --where '{"name":"<field_name>","dimMetId":<dim_met_id>,"op":"in","val":["<value>"]}' \
  --routine weekly

python3 bin/byteworker-cli.py source capture \
  --source-type aeolus --kb "<知识库目录>" \
  --source-uid "aeolus:<region>:<app_id>:<dashboard_id>:<sheet_id>" \
  --out "<系统临时目录>/aeolus-capture.json"

python3 bin/byteworker-cli.py source diff \
  --previous "<上一份完整 capture.json>" \
  --current "<本次完整 capture.json>" \
  --out "<系统临时目录>/aeolus-diff.json"
```

不固定 report 子集时省略所有 `--report-id`，profile 保存
`report_selector.mode=all`，每次 capture 以该 sheet 当前报表集合为准。不启用 routine 时使用
`--routine off`。

## 2. 筛选语义

- `dashboard`（默认）：每次重解析 sheet 的非空 public filters，并只施加到其 `chartIDs`
  覆盖的报表。隐藏筛选也重放。
- `explicit`：忽略 dashboard 默认值；profile 至少保存一个 canonical `where`，用于固定口径。
- `merge`：先取 dashboard 默认值，再按相同 `dimMetId` 用 profile 的 `where` 覆盖。
- register 会验证 report ID 属于该 sheet，并验证显式筛选字段存在于所选报表 dataset。
  `where` 必须含 `name / dimMetId / op / val`。
- dashboard 模式下 UI 筛选变化会改变 snapshot hash。长期指标需要固定口径时，使用
  `explicit` 或 `merge`，并在 reading 中说明。

## 3. 查询、规范化与完整性

- 每个选中报表由原生只读客户端独立重放保存态 VizQuery；任一报表失败，整次 capture 失败，
  不生成部分快照。
- inspect 同时读取 public filters 和 dataset fields。query 使用 dataset 的稳定字段 ID 与真名，
  不拿 UI label 猜字段。每个报表当前只支持一个主 dataset；多 dataset 报表失败闭合。
- 普通表格按 `columns` 映射行；卡片的嵌套对象按返回字段顺序映射；helper 图表先验证指标名，
  再按维度 pivot。结构无法精确对应时返回 `SOURCE_AEOLUS_NORMALIZATION_ERROR`。
- 每个报表是稳定 diff 单元：`record_id=report:<report_id>`。snapshot 保存本次实际选择器、
  effective filters、columns、规范化 rows 和行数。
- `captured_at` 和 query `request_id` 不进入 snapshot hash。风神未返回数据更新时间时必须保存
  `freshness.status=unknown`，不得把“刚查询”表述为“底层数据截至今天”。
- 每个报表生成 `kind=aeolus_report` 的 exact anchor，locator 包含完整坐标、report、dataset
  与本次 effective filters。

## 4. 交给 digest 事务

从 capture 返回值构造 source manifest；所有实例值必须直接取 capture/profile 回执，不手填：

```json
{
  "type": "aeolus",
  "uid": "<capture.source_uid>",
  "url": "<capture.source_url>",
  "title": "<capture.title>",
  "profile_path": "<capture.source_profile.path>",
  "profile_revision": "<capture.source_profile.revision>",
  "region": "<capture.coordinates.region>",
  "app_id": "<capture.coordinates.app_id>",
  "dashboard_id": "<capture.coordinates.dashboard_id>",
  "sheet_id": "<capture.coordinates.sheet_id>",
  "report_ids": "<capture.requested_report_ids>",
  "filter_mode": "<capture.filter_mode>",
  "where_filters": "<capture.where_filters>",
  "components": [{
    "name": "snapshot",
    "kind": "records",
    "path": "<系统临时目录>/aeolus-capture.json",
    "json_pointer": "/snapshot",
    "mode": "canonical-json",
    "heading": "风神看板快照"
  }]
}
```

raw frontmatter 保存 profile path/revision 和本次实际参数，作为历史执行证据；后续调度只读
profile，不从 raw 恢复配置。把 capture 的 `anchors[]` 原样放入 plan 的
`provenance.anchors`，另补 source anchor。

首次 digest 创建代表该看板口径的 `reading`；同源新快照更新同一 reading。普通数值变化只留
raw + provenance；明确越过用户阈值、连续多期显著变化、口径异常或已生效的资源决策，才晋升
为 event/project/decision/area。不要每个报表、每次数值波动各建节点。

## 5. 定期摄取

是否纳入 routine 以及 cadence 都保存在该 profile 的 `routine`，不写进 skill，也不依赖最近
raw。运行时从 INDEX 取得 `source_uid`，再执行 profile capture。完整 snapshot hash 相同只记录
“已复查、无变化”；不同时运行 `source diff`，只把 changed reports 交给语义层，raw 仍保存
完整快照及本次 profile revision。

授权过期、profile 不存在/损坏、source identity 变化、筛选解析失败、任一报表失败或规范化不确定
时，中止该来源，不写部分 raw，也不标为复查成功。旧风神 raw 若尚无 profile，先依据原始来源和
用户确认重新 register；不得静默把 raw 参数提升为未来配置。
