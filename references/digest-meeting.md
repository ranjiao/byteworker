# byteworker · digest 细则 —— 会议簇(日历会议 + 投屏文档 + 妙记)

> 由 `SKILL.md`「digest」一节路由到这里。摄取一场「日历会议 + N 个投屏文档 + 1 个妙记」前必读本文件。

公司用飞书开会的典型形态:一个日历日程 → 主讲人投屏分享一个或多个会议文档 → 会上讨论产出一份妙记(`feishu_minutes`)。这三类物件描述的是**同一场会**,digest 必须当成一个整体处理。

## 核心原则:一场会 = 一个 `event`

这场会只产 **1 个 `event`**;日历日程、N 个投屏文档、1 个妙记全部扇入这同一个 event。**绝不**把同一场会的文档和妙记拆成互不相连的多个 event。

## 触发与发现(主路径:从日历日程反查)

触发:用户给一个**飞书日历会议链接 / 日程**,或自然语言说「把今天的 XX 会议存进来」「digest 这场会」。

1. 用 `lark-calendar` 取该日程 —— 拿到会议标题、起止时间、参会人名单、会议描述 / 附件。
2. 从日程的描述 / 附件里**提取绑定的文档链接**;并尝试定位这场会的妙记:优先用 `lark-vc` 按会议名 / 时间 / 参会人找会议产物与 minute token,再用妙记能力取总结、待办、章节、逐字稿。
3. 若入口是妙记 URL / 会议号而不是日历日程,也要 best-effort 反查对应会议文档:用会议标题、发生时间、参会人去日历 / 会议产物里找附件;同时扫描妙记总结、章节、逐字稿里的飞书文档链接和 `<cite type=doc>` 引用。找到的文档链接纳入 `related_source_urls` 和 event「事件信息」。
4. **把发现的物件列给用户确认** —— 「这场会我找到:文档 A、文档 B、妙记 C,按这些摄取?」。日历里抓不全很常见(妙记 / 文档未必绑定在日程上)→ **明说哪些没找到,请用户补链接**,不静默忽略。
5. 用户也可绕过日历、把这场会的多个 URL(妙记 + 文档)一起丢进 `digest`,或自然语言点明「这些是同一场会」 → 同样按本文件处理。

上述日历 / 妙记 / 投屏文档是**同一会议簇的组成物件**,用第 4 步一次确认即可。它们正文里另行
引用的历史会议、前置方案、数据表或其它文档属于**上下文依赖**,另按
`references/digest-dependencies.md` 判断;不要因为会议簇已确认就自动递归摄取。

## 各物件的角色

- **妙记 `feishu_minutes`(event 主干)** —— 用 `lark-vc` / `lark-minutes` 取纪要 + AI 产物(总结 / 待办 / 章节)+ 逐字稿。它提供:真实讨论、与会人、结论、待办、`decision`,以及「参与方立场分析」的**逐字证据源**。event 的「议程与讨论 / 结论 / 待办 / 参与方立场分析」主要据它来写。
- **日历日程** —— 提供 event 元信息:标题(给 event 命名)、准确时间、参会人名单(校对妙记里识别出的发言人)。
- **投屏文档 `feishu_doc`(按性质分流)** —— 摄取前读 `references/digest-doc.md`。逐个判断:
  - **纯会议材料**(汇报 deck、议程、一次性 slides,会后无独立生命周期)→ 要点并入 event 的「议程与讨论」,文档登记进 event 正文 + `sources`,raw 逐字留存。**不为它单独建 event / project**。
  - **实质枢纽文档**(PRD、规划文档、设计文档,会后仍持续存在)→ 按 `digest-doc.md` **独立 digest**(正常扇出 project / area / decision),再与本场 event **双向 link**。滚动周会文档等属此类,别被一场会吞掉。
  - 判断不准 → 问用户。

## raw_data

**每个物件各落一份逐字 raw**,只增不改:妙记一份(`source_type: feishu_minutes`)、每个文档各一份(`feishu_doc`)。event 节点的 `sources` 同时引用全部 raw —— 溯源不丢。
每个物件在进入 batch 前先各自生成 SourceBundle：妙记走 `feishu_minutes` adapter，投屏文档走
`feishu_doc` adapter。会议簇本身仍是上层编排与 `digest-batch-plan/v2`，不得把日历、妙记和
多个文档压成一个虚构的单来源 Bundle。
每份 raw 的 `source_url` 写该物件自己的原始链接;同一场会中其它已确认物件写入 `related_source_urls`。
输入大(长逐字稿 / 多个长文档)→ 加读 `references/digest-large.md`,委派子 agent 在隔离上下文里摄取。

## 端到端执行参考

下面是「已有 meeting_id / 日程 → 会议簇原子落库」的最短成功路径。业务 artifact、request、
Bundle、plan 和候选节点必须全部放系统临时目录或知识库目录。

### 1. 定位并确认会议物件

1. 用 `lark-vc` 根据 meeting_id 读取会议详情和产物，取得 minute token；再用
   `lark-minutes` 取得妙记元数据、总结/待办/章节和逐字稿。
2. 用日历描述/附件、会议产物和妙记里的文档引用定位投屏文档；用 `lark-doc +fetch
   --api-version v2 --detail with-ids` 抓取正文，并按 `digest-doc.md` 另抓评论/白板。
3. 把找到和没找到的物件一次列给用户确认。只有确认后的妙记与文档进入本次 batch。

不要猜 provider request。先查询实际契约：

```bash
python3 bin/byteworker-cli.py source bundle-spec --source-type feishu_minutes
python3 bin/byteworker-cli.py source bundle-spec --source-type feishu_doc
```

`feishu_minutes.source_uid` 直接写 minute token；`feishu_doc.source_uid` 直接写
document_id/wiki token，二者都不添加 source type 前缀。

### 2. 生成各自的 request 与 Bundle

以下示例假设妙记逐字稿已保存为纯文本，文档 fetch 的完整 JSON 保存在临时目录。lark-cli 的
文档正文位于 `data.document.content` 时，直接使用 `verbatim + json_pointer`，无需额外提取
纯文本：

```bash
BYTEWORKER_MEETING_TMP=$(mktemp -d)

jq -n \
  --arg uid "<minute_token>" \
  --arg url "<minutes_url>" \
  --arg title "<meeting_title>" \
  --arg transcript "$BYTEWORKER_MEETING_TMP/minutes-transcript.txt" \
  '{source_uid:$uid,source_url:$url,title:$title,
    transcript:{path:$transcript}}' \
  > "$BYTEWORKER_MEETING_TMP/minutes-request.json"

python3 bin/byteworker-cli.py source bundle \
  --source-type feishu_minutes \
  --request "$BYTEWORKER_MEETING_TMP/minutes-request.json" \
  --out "$BYTEWORKER_MEETING_TMP/minutes-bundle.json"

jq -n \
  --arg uid "<document_id_or_wiki_token>" \
  --arg url "<document_url>" \
  --arg title "<document_title>" \
  --arg revision "<revision_id>" \
  --arg fetch "$BYTEWORKER_MEETING_TMP/doc-fetch.json" \
  '{source_uid:$uid,source_url:$url,title:$title,revision:$revision,
    body:{path:$fetch,mode:"verbatim",
          json_pointer:"/data/document/content"},
    provider_metadata:{comments_status:"unavailable"}}' \
  > "$BYTEWORKER_MEETING_TMP/doc-request.json"

python3 bin/byteworker-cli.py source bundle \
  --source-type feishu_doc \
  --request "$BYTEWORKER_MEETING_TMP/doc-request.json" \
  --out "$BYTEWORKER_MEETING_TMP/doc-bundle.json"
```

示例把评论标成 unavailable 只是展示最小形状。真实摄取必须按 `digest-doc.md` 拉取评论；可用时
在 request 加 `comments` component 和真实 coverage，不得沿用示例伪装未读取内容。

### 3. 逐来源 preflight

```bash
python3 bin/byteworker-cli.py digest-txn preflight \
  --kb "<知识库目录>" \
  --source "$BYTEWORKER_MEETING_TMP/minutes-bundle.json"

python3 bin/byteworker-cli.py digest-txn preflight \
  --kb "<知识库目录>" \
  --source "$BYTEWORKER_MEETING_TMP/doc-bundle.json"
```

任一来源为 `noop/resume_failed` 时，先按 receipt 处理，不把它悄悄混进新 batch。

### 4. 生成 batch v2 plan

以 [`templates/digest-batch-plan-v2.json`](../templates/digest-batch-plan-v2.json) 为字段参考，
在临时目录生成 plan：

- `inputs[0].source_bundle` 指向妙记 Bundle；
- `inputs[1].source_bundle` 指向文档 Bundle；更多文档继续追加 input；
- 每个 input 分配唯一 `raw_id/raw.path`，`provenance` 只写 `enrichment`；
- event 完整候选的 `sources` 包含全部本批 raw id；
- `source_raw_ids`、`primary_source` 和每条 evidence 的 `raw_id/anchor_id` 显式填写。

不得在 batch v2 复制 `source` 或 `provenance.anchors`。`digest-batch-plan/v1` 只兼容历史
调用，不用于新会议簇。

### 5. 校验并一次执行

```bash
python3 bin/byteworker-cli.py digest-txn validate \
  --kb "<知识库目录>" \
  --plan "$BYTEWORKER_MEETING_TMP/meeting-batch-plan.json"

python3 bin/byteworker-cli.py digest-txn execute \
  --kb "<知识库目录>" \
  --plan "$BYTEWORKER_MEETING_TMP/meeting-batch-plan.json"
```

只有 execute 返回 `data.status=committed` 和 commit hash，且 receipt 显示全部 raw 与同一个
event target，才算会议簇完成。正确结果是 N 份不可变 raw、1 个 event、一次 INDEX 重建和一个
本地 commit。

## 扇出与去重

- **1 个 `event`** = 这场会的快照(妙记主干 + 文档材料 + 日历元信息)。
- event「事件信息」必须列出可打开的来源链接:日历日程(若有)、妙记、每个会议文档 / 投屏文档。
  若已尝试查找但没找到会议文档,可以写“会议文档:未找到”;不得把不确定链接当作会议文档。
- **N 个 `decision`** = 从「妙记 + 文档」**并集**抽取;同一个决策(文档里写了、会上又拍了)**只建一个** decision 节点,挂到 event 和相关 project / 文档节点上,不重复。
- **实体节点** 走标准实体消解(`person` 按 `feishu_id`,见 `references/digest-core.md`「digest 扇出」)。
- **立场分析的素材区分**:投屏文档是主讲人**预设的框架** → 进 `project`/`area`「思路与视角」的【主张】/【意图】素材;妙记逐字稿是各方**真实发言** → 「参与方立场分析」的证据。两者别混。
- 全部互链 `links`,登记进各 raw 的 `digest_targets`。

## 顺序无关兜底

用户可能先 digest 了文档、过一会儿再 digest 妙记。digest 妙记(或后到的文档)时做关联检测:发现近期有**同名 / 同时间**的会议文档或 event → **提示用户「这是不是同一场会、要不要并进同一个 event」**,由用户裁决 —— 不静默另起一个 event,也不静默合并。
