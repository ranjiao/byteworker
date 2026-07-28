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

    def test_postflight_runs_only_after_a_real_fast_forward(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            seed = root / "seed"
            (seed / "bin").mkdir(parents=True)
            shutil.copy2(ROOT / "bin/update-check.sh", seed / "bin/update-check.sh")
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


if __name__ == "__main__":
    unittest.main()
