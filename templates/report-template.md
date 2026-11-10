# report-template.html 渲染契约

Dreaming 报告使用 v2 HTML 模板。界面采用冷色深色主题，用一种强调色。

模板必须是自包含的单文件，禁止引用外部 JS、CSS、字体、图片或网络 URL。占位符用 `{{NAME}}` 格式。
Python 渲染器先转义业务文本，再替换占位符。

## 占位符

- 单值字段为 `TITLE`、`GENERATED_AT`、`WINDOW_START`、`WINDOW_END`、`TIMEZONE`、
  `COVERAGE_STATUS`、`CONFIDENCE` 和 `MANUAL_NOTES`。
- HTML 片段为 `STATS`、`SOURCE_TABS`、`HIGHLIGHTS`、`SECTION_CARDS`、`SOURCES` 和
  `COVERAGE_NOTES`。

## HTML 片段

按下面的结构生成片段。内联脚本用 `data-*` 属性过滤来源、展开或收起条目，以及跳到引用来源。

### `STATS`

每项指标生成一个单元。要突出显示数值时，例如高优先级事项数，给 `.stat-v` 加上 `accent`。

```html
  <div class="stat"><span class="stat-k">ITEMS</span><span class="stat-v">25</span></div>
```

### `SOURCE_TABS`

第一个标签必须是 `data-tab="all"`。

```html
  <button type="button" class="tab is-on" data-tab="all">全部来源<span class="pill" data-count="all">25</span></button>
  <button type="button" class="tab" data-tab="S1">《内部例会文档》<span class="pill" data-count="S1">11</span></button>
```

### `HIGHLIGHTS`

选当天最重要的 3–6 条事项。`data-src` 中的来源 ID 用空格分隔。等级类用 `lv-high`、`lv-attn` 或 `lv-info`。

```html
  <button type="button" class="card item lv-attn" data-item data-src="S1">
    <span class="row-top">
      <span class="row-left"><span class="num">01</span><span class="lv">关注</span></span>
      <span class="hint">展开</span>
    </span>
    <span class="card-title">标题</span>
    <span class="lede">首句摘要。</span>
    <span class="detail">展开后的补充说明。</span>
    <span class="refs"><a class="ref" href="#src-S1">[来源一] 高</a></span>
  </button>
```

### `SECTION_CARDS`

关键进展、决策与对齐、风险和明日关注各用一个区块。保留四列表头，每行用 `.item` 按钮。

```html
  <section class="block" data-block>
    <div class="block-head"><span class="bar lv-info"></span><h2>关键进展</h2>
      <span class="code">Progress</span><span class="spacer"></span>
      <span class="mono muted"><span data-block-count>5</span> 条</span></div>
    <div class="thead"><span>对象</span><span>结论 / 说明</span><span>等级</span><span>来源</span></div>
    <button type="button" class="item row lv-info" data-item data-src="S1">
      <span class="subj">业务能力甲<span class="tick"></span></span>
      <span class="cell">
        <span class="row-title">标题</span>
        <span class="lede">首句摘要。</span>
        <span class="detail">补充说明。</span>
      </span>
      <span class="lv">常规</span>
      <span class="cell">
        <a class="ref" href="#src-S1">[来源一]</a>
        <span class="hint">展开</span>
      </span>
    </button>
  </section>
```

### `SOURCES`

每个来源都是一个跳转目标，ID 必须是 `src-<source id>`。

```html
  <div class="src" id="src-S1">
    <span class="cell"><span class="mono ref-static">[来源一]</span><span class="mono kind">文档</span></span>
    <span class="cell"><span class="src-name">《内部例会文档》</span><span class="src-meta">飞书文档 · 原始出处:已脱敏 …</span></span>
    <span class="mono conf conf-high">可信度 高</span>
  </div>
```

### `COVERAGE_NOTES`

每条覆盖说明用一个 `<span class="note">…</span>`。
