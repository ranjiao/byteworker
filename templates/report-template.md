# report-template.html — 渲染契约

Byteworker Dreaming report HTML template — v2 (cool dark / single accent).

Design contract:
- Self-contained only. No external JS/CSS/fonts/images/network URLs.
- Keep placeholders wrapped as { {NAME} }. The Python renderer escapes all
  business text before replacement.

Placeholders (scalars):
  { {TITLE} } { {GENERATED_AT} } { {WINDOW_START} } { {WINDOW_END} } { {TIMEZONE} }
  { {COVERAGE_STATUS} } { {CONFIDENCE} } { {MANUAL_NOTES} }

Placeholders (HTML fragments the renderer builds — patterns below):
  { {STATS} } { {SOURCE_TABS} } { {HIGHLIGHTS} } { {SECTION_CARDS} } { {SOURCES} }
  { {COVERAGE_NOTES} }

FRAGMENT PATTERNS — copy these shapes exactly; the inline script relies on
the data-* attributes for source filtering, expand/collapse and ref jumps.

{ {STATS} } — one per metric cell:
  <div class="stat"><span class="stat-k">ITEMS</span><span class="stat-v">25</span></div>
  (add class "accent" on .stat-v to highlight, e.g. the high-priority count)

{ {SOURCE_TABS} } — first tab must be data-tab="all":
  <button type="button" class="tab is-on" data-tab="all">全部来源<span class="pill" data-count="all">25</span></button>
  <button type="button" class="tab" data-tab="S1">《内部例会文档》<span class="pill" data-count="S1">11</span></button>

{ {HIGHLIGHTS} } — today's 3-6 top signals. data-src = space-separated source ids.
Level class is lv-high | lv-attn | lv-info:
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

{ {SECTION_CARDS} } — one block per section (关键进展 / 决策与对齐 / 风险 / 明日关注 / Todo).
Keep the 4-column head row; each row is an .item button:
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

{ {SOURCES} } — the jump targets; id must be src-<source id>:
  <div class="src" id="src-S1">
    <span class="cell"><span class="mono ref-static">[来源一]</span><span class="mono kind">文档</span></span>
    <span class="cell"><span class="src-name">《内部例会文档》</span><span class="src-meta">飞书文档 · 原始出处:已脱敏 …</span></span>
    <span class="mono conf conf-high">可信度 高</span>
  </div>

{ {COVERAGE_NOTES} } — one <span class="note">…</span> per note.
