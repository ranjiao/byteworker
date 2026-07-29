from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ViewerLayoutContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viewer = (ROOT / "viewer/index.html").read_text(encoding="utf-8")

    def test_structured_sections_share_a_body_baseline(self):
        self.assertIn("padding-left: 30px;", self.viewer)
        self.assertIn("grid-template-columns: 22px minmax(0, 1fr)", self.viewer)
        self.assertIn("grid-template-columns: 112px minmax(0, 1fr)", self.viewer)

    def test_timeline_accepts_chinese_colon_dates_without_empty_date_column(self):
        self.assertIn(r"(?:\s*[:：—]\s*|\s+)", self.viewer)
        self.assertIn('class="sx-tl-rail"', self.viewer)
        self.assertNotIn('class="sx-tl-date"', self.viewer)

    def test_event_dependency_and_reference_sections_do_not_create_empty_columns(self):
        dependency_rule = "if (/未摄取依赖|关联来源/.test(t)) return 'dependencies';"
        info_rule = "if (/基本信息|事件信息|来源/.test(t)) return 'info';"
        self.assertLess(self.viewer.index(dependency_rule), self.viewer.index(info_rule))
        self.assertIn("sx-refs-row sx-refs-row-full", self.viewer)
        self.assertNotIn("'<div></div>'}<div class=\"sx-refs-chips\"", self.viewer)

    def test_unpunctuated_node_relationship_gets_a_label_column(self):
        self.assertIn("function sxNoderefParts(raw)", self.viewer)
        self.assertIn("label = relation[1] + '节点';", self.viewer)
        self.assertIn("const rows = items.map(sxNoderefParts);", self.viewer)
        self.assertIn("更新|关联|新增|涉及|影响", self.viewer)

    def test_org_members_link_unique_known_people(self):
        self.assertIn("function sxKnownNodeLinks(text, type)", self.viewer)
        self.assertIn("titleCounts.get(title) === 1", self.viewer)
        self.assertIn("sxKnownNodeLinks(p, 'person')", self.viewer)
        self.assertIn("sxKnownNodeLinks(r.rest, 'person')", self.viewer)
        self.assertIn("nodeLinkHTML(match.id)", self.viewer)

    def test_related_source_urls_keep_an_obvious_link_style(self):
        self.assertIn(".sx-ref-text a:not(.evidence-ref)", self.viewer)
        self.assertIn("border-bottom-color: var(--accent);", self.viewer)
        self.assertIn('content: "↗"', self.viewer)
        self.assertIn("([。；，！？、])(?=$|\\s)", self.viewer)

    def test_conclusions_use_a_real_marker_instead_of_an_empty_lead_column(self):
        self.assertIn('class="sx-conclusion-mark">✓</span>', self.viewer)
        self.assertNotIn(
            "${r.lead ? `<div class=\"sx-conclusion-lead\">${esc(r.lead)}</div>` : '<div></div>'}",
            self.viewer,
        )

    def test_decision_status_is_an_inline_row_not_a_badge(self):
        self.assertIn("/* status: quiet inline rows, not attention-grabbing badges */", self.viewer)
        self.assertIn("background: transparent; color: var(--ink);", self.viewer)

    def test_evidence_table_protects_source_and_locator_columns(self):
        self.assertIn(".sx-evidence .prose td:nth-child(2) { min-width: 220px; }", self.viewer)
        self.assertIn(".sx-evidence .prose td:nth-child(3) { min-width: 360px; }", self.viewer)
        self.assertIn("width: max-content; min-width: 100%; table-layout: auto;", self.viewer)


if __name__ == "__main__":
    unittest.main()
