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
            "bin/byteworker",
            "bin/byteworker-launcher.py",
            "bin/session-preflight.py",
            "bin/byteworker-cli.py",
            "bin/digest-txn.py",
            "bin/source.py",
            "bin/kb-query.py",
            "lib/machine_protocol.py",
            "lib/runtime_deps.py",
            "lib/session_preflight.py",
            "lib/digest_txn.py",
            "lib/doctor_sources.py",
            "lib/kb_query.py",
            "lib/source_operations.py",
            "lib/source_chat_operations.py",
            "lib/source_profiles.py",
            "lib/source_profile_providers.py",
            "lib/source_profile_contract.py",
            "lib/credential_safety.py",
            "lib/kb_write_txn.py",
            "lib/kb_mutation.py",
            "lib/context_view.py",
            "lib/semantic_policy.py",
            "bin/wiki.py",
            "lib/wiki_explorer.py",
            "bin/digest-job.py",
            "lib/digest_jobs.py",
            "bin/report-automation.py",
            "lib/report_automation.py",
            "bin/dreaming.py",
            "lib/dreaming_state.py",
            "lib/dreaming_models.py",
            "lib/dreaming_grants.py",
            "lib/dreaming_collection.py",
            "lib/dreaming_batch.py",
            "lib/dreaming_collectors/feishu_im.py",
            "lib/dreaming_analysis.py",
            "lib/dreaming_consolidation.py",
            "lib/dreaming_process.py",
            "lib/dreaming_action_policy.py",
            "lib/dreaming_action_ledger.py",
            "lib/dreaming_reports.py",
            "lib/report_owner.py",
            "lib/dreaming_evaluation.py",
            "lib/dreaming_scheduler.py",
            "bin/index.py",
            "lib/snapshot_store.py",
            "lib/source_capture.py",
            "lib/sources/models.py",
            "lib/sources/registry.py",
            "lib/sources/request_specs.py",
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

    def test_runtime_cache_is_persistent_until_invalid(self):
        for term in (
            "缓存没有 TTL",
            "路径被删除、失去执行权限或解释器不再兼容时才重新扫描",
            "`deps --refresh`",
            "`runtime-reset`",
        ):
            self.assertIn(term, self.architecture)

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
            "`digest-batch-plan/v2`",
            "### 8.4 架构验证矩阵",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.architecture)

    def test_dreaming_is_opt_in_and_decoupled_from_existing_commands(self):
        for term in (
            "Dreaming 不属于公共 preflight",
            "缺失状态必须等同关闭",
            "拒绝接管 daily/weekly",
            "`byteworker-dreaming/v2`",
            "v1→v2",
            "`0700/0600`",
            "不得改变 digest/search/update 等",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.architecture)

        for relative_path in (
            "lib/digest_txn.py",
            "lib/kb_query.py",
            "lib/session_preflight.py",
            "lib/report_automation.py",
        ):
            with self.subTest(relative_path=relative_path):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotIn("dreaming_scheduler", source)

        dreaming_source = (ROOT / "lib/dreaming_scheduler.py").read_text(
            encoding="utf-8"
        )

        for forbidden_import in (
            "import digest_txn",
            "from digest_txn",
            "import kb_query",
            "from kb_query",
            "import report_automation",
            "from report_automation",
        ):
            with self.subTest(forbidden_import=forbidden_import):
                self.assertNotIn(forbidden_import, dreaming_source)
        state_source = (ROOT / "lib/dreaming_state.py").read_text(
            encoding="utf-8"
        )
        models_source = (ROOT / "lib/dreaming_models.py").read_text(
            encoding="utf-8"
        )
        dreaming_sources = [
            state_source,
            models_source,
            (ROOT / "lib/dreaming_grants.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_collection.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_batch.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_analysis.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_consolidation.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_process.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_action_policy.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_action_ledger.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_reports.py").read_text(encoding="utf-8"),
            (ROOT / "lib/report_owner.py").read_text(encoding="utf-8"),
            (ROOT / "lib/dreaming_evaluation.py").read_text(encoding="utf-8"),
        ]
        for source in dreaming_sources:
            self.assertNotIn("import digest_txn", source)
            self.assertNotIn("from digest_txn", source)
            self.assertNotIn("import kb_query", source)
            self.assertNotIn("from kb_query", source)
        self.assertNotIn("source-architecture-refactor.md", self.architecture)

    def test_inbox_removal_boundary_is_documented(self):
        for term in (
            "`bin/inbox.py`",
            "`INBOX_REMOVED`",
            "新 KB 不创建 `reports/im/`",
            "历史目录",
            "foreground `process once`",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.architecture)

    def test_dreaming_tour_and_maintenance_boundaries_are_documented(self):
        for term in (
            "能力导览",
            "process / morning / maintenance / recovery",
            "`maintenance`",
            "`DOCTOR_USER_DECISION_REQUIRED`",
            "`waiting_for_user`",
            "公开 `doctor scan/fix` facade",
        ):
            with self.subTest(term=term):
                self.assertIn(term, self.architecture)


if __name__ == "__main__":
    unittest.main()
