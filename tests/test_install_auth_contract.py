from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallAuthContractTests(unittest.TestCase):
    def test_installation_requires_opt_in_and_runtime_safe_auth_flow(self):
        install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
        self.assertIn("是否现在启用 Meego / 多维表格 / 风神定期摄取", install)
        self.assertIn("稍后再说", install)
        self.assertIn("source auth-status --source-type meego", install)
        self.assertIn("source auth-status --source-type feishu_base", install)
        self.assertIn("source auth-status --source-type aeolus", install)
        self.assertIn("BYTEWORKER_AEOLUS_CLIENT_ID", install)
        self.assertNotIn("bytedcli", install.lower())
        self.assertIn("--no-wait --json", install)
        self.assertIn("lark-cli auth qrcode", install)
        self.assertIn("lark-cli auth login --device-code", install)
        self.assertIn("不要重复登录或自动改用 bot", install)

    def test_runtime_skill_uses_the_same_auth_guard(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("source auth-status", skill)
        self.assertIn("source inspect / capture", skill)
        self.assertIn("禁止用重复登录或静默切 bot 掩盖", skill)

    def test_aeolus_runtime_uses_native_client_not_external_cli(self):
        source = (ROOT / "bin" / "source.py").read_text(encoding="utf-8")
        client = (ROOT / "lib" / "aeolus_client.py").read_text(encoding="utf-8")
        self.assertIn("aeolus_client_from_environment", source)
        self.assertNotIn("BYTEWORKER_BYTEDCLI", source)
        self.assertNotIn("subprocess", client)


if __name__ == "__main__":
    unittest.main()
