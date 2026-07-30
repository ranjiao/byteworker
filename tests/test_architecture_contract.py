import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MERMAID_BLOCK_RE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)


class ArchitectureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.architecture = (ROOT / "ARCHITECTURE.md").read_text(
            encoding="utf-8"
        )

    def test_architecture_covers_flow_code_and_governance(self):
        for heading in (
            "## 2. 整个 skill 的信息处理流程",
            "## 4. 代码层面的模块架构",
            "## 5. 跨层契约",
            "## 6. 失败边界与安全策略",
            "## 8. 架构治理：每次开发必须执行",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.architecture)

        blocks = MERMAID_BLOCK_RE.findall(self.architecture)
        self.assertGreaterEqual(len(blocks), 10)
        allowed_headers = {
            "flowchart LR",
            "flowchart TD",
            "flowchart TB",
            "sequenceDiagram",
        }
        for block in blocks:
            with self.subTest(block=block[:60]):
                self.assertIn(block.splitlines()[0], allowed_headers)

    def test_architecture_names_current_core_modules(self):
        core_paths = (
            "bin/byteworker-cli.py",
            "bin/digest-txn.py",
            "bin/source.py",
            "bin/kb-query.py",
            "lib/machine_protocol.py",
            "lib/digest_txn.py",
            "lib/doctor_sources.py",
            "lib/kb_query.py",
            "lib/source_operations.py",
            "lib/source_chat_operations.py",
            "lib/source_profiles.py",
            "lib/source_profile_providers.py",
            "bin/wiki.py",
            "lib/wiki_explorer.py",
            "bin/digest-job.py",
            "lib/digest_jobs.py",
            "bin/report-automation.py",
            "lib/report_automation.py",
            "lib/snapshot_store.py",
            "lib/source_capture.py",
            "lib/sources/models.py",
            "lib/sources/registry.py",
            "lib/sources/transaction_bridge.py",
            "lib/sources/record_projection.py",
        )
        for relative_path in core_paths:
            with self.subTest(path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())
                self.assertIn(relative_path, self.architecture)

    def test_architecture_governance_is_wired_into_agent_entrypoints(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## 架构治理", agents)
        self.assertIn("[`ARCHITECTURE.md`](ARCHITECTURE.md)", agents)
        self.assertIn("同一变更", agents)
        self.assertIn("[`ARCHITECTURE.md`](ARCHITECTURE.md)", skill)
        self.assertIn("同一变更", skill)
        self.assertIn("[`ARCHITECTURE.md`](ARCHITECTURE.md)", readme)

    def test_provider_neutral_core_rule_is_explicit(self):
        self.assertIn(
            "`digest_txn.py` 和 `kb_query.py` 是 provider-neutral core",
            self.architecture,
        )
        self.assertIn(
            "`lib/digest_txn.py` 来加入 provider 名称判断",
            self.architecture,
        )
        self.assertIn(
            "`lib/kb_query.py` 来解析新的 provider 私有结构",
            self.architecture,
        )

    def test_source_final_contract_is_not_split_into_a_process_ledger(self):
        self.assertFalse(
            (ROOT / "references/source-architecture-refactor.md").exists()
        )
        for term in (
            "#### 4.3.1 最终领域模型",
            "`SourceRef`",
            "`CaptureProfile`",
            "`SourceCapabilities`",
            "`SourceBundle`",
            "`SnapshotStore`",
            "`ChangeSet`",
            "`DigestPlan`",
            "### 8.4 架构验证矩阵",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.architecture)
        self.assertNotIn("source-architecture-refactor.md", self.architecture)


if __name__ == "__main__":
    unittest.main()
