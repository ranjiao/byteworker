from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReportSchedulingContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_user_facing_docs_remove_daily_and_weekly_commands(self):
        combined = "\n".join(
            self.read(path)
            for path in (
                "SKILL.md",
                "README.md",
                "TUTORIAL.md",
                "references/help.md",
            )
        )
        self.assertNotIn("/byteworker daily", combined)
        self.assertNotIn("/byteworker weekly", combined)
        self.assertIn("没有 `daily` / `weekly` 用户子命令", combined)

    def test_install_handles_fresh_and_upgraded_users_once(self):
        install = self.read("INSTALL.md")
        skill = self.read("SKILL.md")
        scheduling = self.read("references/report-scheduling.md")
        for term in (
            "report-automation status",
            "needs_onboarding=true",
            "Codex 桌面端",
            "Claude Code Desktop",
            "TRAE Work 桌面端",
            "Run now",
        ):
            with self.subTest(term=term):
                self.assertIn(term, install)
        self.assertIn("references/report-scheduling.md", skill)
        self.assertIn("一次性迁移", scheduling)
        self.assertIn("decision --value prompted", scheduling)
        self.assertIn("declined", scheduling)
        self.assertIn("deferred", scheduling)

    def test_every_automatic_report_runs_all_routine_sources_first(self):
        for path in (
            "SKILL.md",
            "references/report-scheduling.md",
            "references/periodic-report.md",
            "templates/report-automation-daily.md",
            "templates/report-automation-weekly.md",
        ):
            with self.subTest(path=path):
                text = self.read(path)
                self.assertIn("routine digest", text)
                self.assertIn("七天", text)
        self.assertIn("已登记的定期来源", self.read("INSTALL.md"))
        daily = self.read("templates/report-automation-daily.md")
        weekly = self.read("templates/report-automation-weekly.md")
        self.assertIn("所有已登记且启用", daily)
        self.assertIn("所有已登记且启用", weekly)

    def test_recovery_task_checks_last_success_before_retrying(self):
        recovery = self.read("templates/report-automation-recovery.md")
        scheduling = self.read("references/report-scheduling.md")
        for term in (
            "report-automation check",
            "should_run=true",
            "last_success",
            "补跑一期",
        ):
            with self.subTest(term=term):
                self.assertIn(term, recovery)
                self.assertIn(term, scheduling)
        self.assertIn("08:30、12:30、18:30、22:30", scheduling)
        self.assertIn(
            "templates/report-automation-recovery.md",
            self.read("INSTALL.md"),
        )

    def test_scheduler_is_local_and_harness_owned(self):
        scheduling = self.read("references/report-scheduling.md")
        architecture = self.read("ARCHITECTURE.md")
        self.assertIn("宿主任务系统仍是真相源", scheduling)
        self.assertIn("系统 cron / launchd", scheduling)
        self.assertIn("宿主本地定时任务", architecture)
        self.assertIn("不承担任务唤醒", architecture)
        self.assertIn("第三个宿主原生任务唤醒", architecture)
        self.assertIn("TRAE IDE/TraeCode", scheduling)
        self.assertIn("切换到 TraeWork 桌面版", scheduling)


if __name__ == "__main__":
    unittest.main()
