import argparse
import ast
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def load_cli(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class CommandRefactorBoundaryTests(unittest.TestCase):
    def test_todo_cli_only_defines_parser_and_io_boundary(self):
        source = (ROOT / "bin" / "todo.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual({"build_parser", "main"}, functions)
        self.assertEqual([], classes)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("kb_write_lock", source)

    def test_dreaming_cli_modules_do_not_import_each_other(self):
        modules = sorted(LIB.glob("dreaming_cli_*.py"))
        self.assertGreaterEqual(len(modules), 6)
        for path in modules:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            forbidden = {
                candidate.stem
                for candidate in modules
                if candidate != path and candidate.stem != "dreaming_cli_common"
            }
            with self.subTest(path=path.name):
                self.assertEqual(set(), imports & forbidden)

    def test_every_dreaming_command_level_has_summary(self):
        dreaming = load_cli("byteworker_dreaming_boundaries", "bin/dreaming.py")

        def assert_parser_summaries(parser: argparse.ArgumentParser) -> None:
            for action in parser._actions:
                if not isinstance(action, argparse._SubParsersAction):
                    continue
                summaries = {
                    choice.dest: choice.help for choice in action._choices_actions
                }
                self.assertEqual(set(action.choices), set(summaries))
                self.assertTrue(all(value for value in summaries.values()))
                for child in action.choices.values():
                    assert_parser_summaries(child)

        assert_parser_summaries(dreaming.parser())


if __name__ == "__main__":
    unittest.main()
