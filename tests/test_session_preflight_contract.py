from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SessionPreflightContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_skill_has_one_quiet_session_entrypoint(self):
        skill = self.read("SKILL.md")
        self.assertIn("bin/byteworker preflight", skill)
        self.assertIn("无输出", skill)
        self.assertIn("--require feishu", skill)
        self.assertIn("不要再分别运行", skill)

    def test_architecture_owns_bootstrap_and_preflight(self):
        architecture = self.read("ARCHITECTURE.md")
        for value in (
            "bin/byteworker",
            "lib/runtime_deps.py",
            "lib/session_preflight.py",
            "byteworker-session-preflight/v1",
            "byteworker-runtime-check/v1",
        ):
            self.assertIn(value, architecture)

    def test_machine_protocol_prefers_runtime_safe_launcher(self):
        protocol = self.read("references/machine-protocol.md")
        self.assertIn("bin/byteworker <tool>", protocol)
        self.assertIn("bin/byteworker lark", protocol)
        self.assertIn("bin/byteworker run", protocol)


if __name__ == "__main__":
    unittest.main()
