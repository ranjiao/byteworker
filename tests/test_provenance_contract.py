from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProvenanceContractTests(unittest.TestCase):
    def test_skill_routes_digest_to_provenance_rules(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/provenance.md", skill)
        self.assertIn("primary_source", skill)
        self.assertIn("[E1]", skill)

    def test_viewer_exposes_primary_and_fact_sources(self):
        viewer = (ROOT / "viewer/index.html").read_text(encoding="utf-8")
        self.assertIn("resolveNodePrimarySource", viewer)
        self.assertIn("打开主要来源", viewer)
        self.assertIn("enhanceEvidenceLinks", viewer)
        self.assertIn("打开飞书原文", viewer)
        self.assertIn("查看库内归档", viewer)
        self.assertIn("comment-evidence-link", viewer)
        self.assertIn("打开评论出处", viewer)
        self.assertIn("打开所在文档", viewer)
        self.assertIn(".sx-kv a::after", viewer)
        self.assertIn("sxParseEvidenceRefs", viewer)
        self.assertIn("sxEvidenceRefs[id]", viewer)
        self.assertIn(r"/^\[?E[1-9][0-9]*\]?$/", viewer)

    def test_viewer_info_section_does_not_treat_time_as_a_wide_key(self):
        viewer = (ROOT / "viewer/index.html").read_text(encoding="utf-8")
        self.assertIn("ci > 0 && ci <= 24", viewer)
        self.assertIn("isTimePrefix", viewer)
        self.assertIn('class="sx-kv-full"', viewer)
        self.assertIn("grid-column: 1 / -1", viewer)

    def test_chat_pull_preserves_message_locators(self):
        script = (ROOT / "bin/pull-chat.sh").read_text(encoding="utf-8")
        self.assertIn("--locators-out", script)
        self.assertIn("chat:message:", script)
        self.assertIn("message_id", script)


if __name__ == "__main__":
    unittest.main()
