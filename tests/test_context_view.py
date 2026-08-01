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


if __name__ == "__main__":
    unittest.main()
