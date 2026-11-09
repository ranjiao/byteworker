from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from context_view import ContextViewError, context_view  # noqa: E402


class ContextViewTests(unittest.TestCase):
    def context(self, root: Path, *, focus: str = "focus") -> None:
        root.joinpath("context.md").write_text(
            "# context\n"
            "## 我的身份\nidentity\n"
            "## 我的职责范围\nscope\n"
            f"## 我的当前重点\n{focus}\n"
            "## 主管方向\nmanager\n"
            "## 当前约束\nconstraints\n"
            "## 交互与提醒偏好\npreferences\n"
            "## 背景信息\nbackground\n",
            encoding="utf-8",
        )

    def test_todo_projection_excludes_unneeded_work_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.context(root)

            result = context_view(root, "todo")

            self.assertEqual(
                {"我的身份", "交互与提醒偏好"},
                set(result["sections"]),
            )
            self.assertNotIn("focus", str(result))

    def test_hard_budget_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.context(root, focus="x" * 25_000)

            with self.assertRaises(ContextViewError) as caught:
                context_view(root, "search")

            self.assertEqual("CONTEXT_BUDGET_EXCEEDED", caught.exception.code)

    def test_dreaming_projection_includes_full_semantic_lens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.context(root)
            result = context_view(root, "dreaming")
            self.assertEqual(
                {
                    "我的身份",
                    "我的职责范围",
                    "我的当前重点",
                    "主管方向",
                    "当前约束",
                    "交互与提醒偏好",
                    "背景信息",
                },
                set(result["sections"]),
            )

    def test_removed_inbox_intent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.context(root)
            with self.assertRaises(ContextViewError) as caught:
                context_view(root, "inbox")
            self.assertEqual("CONTEXT_INTENT_INVALID", caught.exception.code)

    def test_preferences_projection_reads_interaction_language_without_work_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("context.md").write_text(
                "# context\n"
                "## 我的身份\n"
                "| 字段 | 内容 |\n"
                "|------|------|\n"
                "| 姓名 | 冉蛟 |\n"
                "| 时区 | Asia/Shanghai |\n"
                "| 交互语言 | 简体中文 |\n\n"
                "## 我的职责范围\nsecret scope\n"
                "## 我的当前重点\nsecret focus\n"
                "## 主管方向\n\n"
                "## 当前约束\n\n"
                "## 交互与提醒偏好\n- 未指定具体时间的提醒：09:00\n"
                "## 背景信息\n",
                encoding="utf-8",
            )

            result = context_view(root, "preferences")

            self.assertEqual("zh-CN", result["interaction_language"])
            self.assertEqual(
                {"我的身份", "交互与提醒偏好"},
                set(result["sections"]),
            )
            self.assertNotIn("secret scope", str(result["sections"]))
            self.assertNotIn("secret focus", str(result["sections"]))

    def test_legacy_preference_language_is_visible_to_every_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath("context.md").write_text(
                "# context\n"
                "## 我的身份\n| 字段 | 内容 |\n|------|------|\n| 姓名 | 冉蛟 |\n\n"
                "## 我的职责范围\nscope\n"
                "## 我的当前重点\nfocus\n"
                "## 主管方向\n\n"
                "## 当前约束\n\n"
                "## 交互与提醒偏好\n- 交互语言：English\n- 未指定具体时间的提醒：09:00\n"
                "## 背景信息\n",
                encoding="utf-8",
            )

            result = context_view(root, "search")

            self.assertEqual("en", result["interaction_language"])

    def test_missing_or_pending_language_remains_empty_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.context(root)

            self.assertEqual("", context_view(root, "todo")["interaction_language"])

            root.joinpath("context.md").write_text(
                "# context\n"
                "## 我的身份\n| 字段 | 内容 |\n|------|------|\n"
                "| 交互语言 | 待补充 |\n\n"
                "## 我的职责范围\nscope\n"
                "## 我的当前重点\nfocus\n"
                "## 主管方向\n\n"
                "## 当前约束\n\n"
                "## 交互与提醒偏好\n"
                "## 背景信息\n",
                encoding="utf-8",
            )

            self.assertEqual("", context_view(root, "search")["interaction_language"])


if __name__ == "__main__":
    unittest.main()
