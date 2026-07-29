import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UpdateCheckTests(unittest.TestCase):
    def run_git(self, cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def copy_update_tools(self, destination):
        (destination / "bin").mkdir(parents=True, exist_ok=True)
        (destination / "lib").mkdir(parents=True, exist_ok=True)
        for relative in (
            "bin/update-check.sh",
            "bin/update-state.py",
            "lib/update_state.py",
        ):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def test_postflight_runs_only_after_a_real_fast_forward(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            seed = root / "seed"
            self.copy_update_tools(seed)
            (seed / "bin/update-postflight.py").write_text(
                "#!/usr/bin/env python3\nprint('doctor:hook-ran')\n",
                encoding="utf-8",
            )
            self.run_git(seed, "init", "-b", "master")
            self.run_git(seed, "config", "user.email", "update@example.test")
            self.run_git(seed, "config", "user.name", "Update Tests")
            self.run_git(seed, "add", ".")
            self.run_git(seed, "commit", "-m", "initial")

            remote = root / "remote.git"
            subprocess.run(
                ["git", "clone", "--bare", str(seed), str(remote)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            installed = root / "installed"
            publisher = root / "publisher"
            for destination in (installed, publisher):
                subprocess.run(
                    ["git", "clone", str(remote), str(destination)],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            no_update = subprocess.run(
                ["bash", str(installed / "bin/update-check.sh"), "--force"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual("", no_update.stdout)

            self.run_git(publisher, "config", "user.email", "update@example.test")
            self.run_git(publisher, "config", "user.name", "Update Tests")
            (publisher / "version.txt").write_text("next\n", encoding="utf-8")
            self.run_git(publisher, "add", "version.txt")
            self.run_git(publisher, "commit", "-m", "next")
            self.run_git(publisher, "push", "origin", "master")

            updated = subprocess.run(
                ["bash", str(installed / "bin/update-check.sh"), "--force"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertIn("skill 已自动更新", updated.stdout)
            self.assertIn("doctor:hook-ran", updated.stdout)
            state = json.loads(
                (installed / ".update-state.json").read_text(encoding="utf-8")
            )
            self.assertFalse(state["postflight_pending"])
            self.assertEqual(
                self.run_git(installed, "rev-parse", "HEAD").stdout.strip(),
                state["last_checked_commit"],
            )

    def test_fetch_failure_retries_before_the_weekly_interval(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            repo = root / "repo"
            self.copy_update_tools(repo)
            self.run_git(repo, "init", "-b", "master")
            self.run_git(repo, "config", "user.email", "update@example.test")
            self.run_git(repo, "config", "user.name", "Update Tests")
            self.run_git(repo, "add", ".")
            self.run_git(repo, "commit", "-m", "initial")
            env = {
                **os.environ,
                "BYTEWORKER_UPDATE_REPO_URL": str(root / "missing.git"),
                "BYTEWORKER_UPDATE_NOW": "100",
            }
            first = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh"), "--force"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertIn("无法连接 GitHub", first.stdout)
            state = json.loads(
                (repo / ".update-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(1, state["failure_count"])
            self.assertEqual(3700, state["next_retry_at"])

            env["BYTEWORKER_UPDATE_NOW"] = "101"
            throttled = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh")],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual("", throttled.stdout)

            env["BYTEWORKER_UPDATE_NOW"] = "3700"
            retried = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh")],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertIn("无法连接 GitHub", retried.stdout)
            state = json.loads(
                (repo / ".update-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(2, state["failure_count"])

    def test_opt_out_and_live_lock_are_silent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            repo = root / "repo"
            self.copy_update_tools(repo)
            disabled = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh"), "--force"],
                env={**os.environ, "BYTEWORKER_NO_AUTO_UPDATE": "1"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual("", disabled.stdout)
            self.assertFalse((repo / ".update-state.json").exists())

            lock = repo / ".update-check.lock"
            lock.mkdir()
            (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
            locked = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh"), "--force"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual("", locked.stdout)
            self.assertFalse((repo / ".update-state.json").exists())

    def test_pending_postflight_retries_without_refetching_code(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            repo = Path(temporary) / "repo"
            self.copy_update_tools(repo)
            (repo / "bin/update-postflight.py").write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "marker = Path(__file__).resolve().parents[1] / 'postflight-count'\n"
                "count = int(marker.read_text() or '0') if marker.exists() else 0\n"
                "marker.write_text(str(count + 1))\n"
                "print('doctor:recovered')\n",
                encoding="utf-8",
            )
            state = {
                "version": 1,
                "last_attempt_at": 100,
                "last_success_at": 100,
                "failure_count": 0,
                "last_failure_code": "",
                "next_retry_at": 0,
                "last_checked_commit": "abc123",
                "postflight_pending": True,
                "postflight_commit": "abc123",
                "postflight_failure_count": 1,
                "postflight_last_failure_code": "exit-1",
                "postflight_next_retry_at": 200,
            }
            (repo / ".update-state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )

            before_retry = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh")],
                env={**os.environ, "BYTEWORKER_UPDATE_NOW": "199"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual("", before_retry.stdout)
            self.assertFalse((repo / "postflight-count").exists())

            recovered = subprocess.run(
                ["bash", str(repo / "bin/update-check.sh")],
                env={**os.environ, "BYTEWORKER_UPDATE_NOW": "200"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertIn("已补跑更新后兼容检查", recovered.stdout)
            self.assertIn("doctor:recovered", recovered.stdout)
            self.assertEqual("1", (repo / "postflight-count").read_text())
            updated_state = json.loads(
                (repo / ".update-state.json").read_text(encoding="utf-8")
            )
            self.assertFalse(updated_state["postflight_pending"])


if __name__ == "__main__":
    unittest.main()
