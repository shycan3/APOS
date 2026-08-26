from pathlib import Path
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest

from apos.kernel import Kernel, RunOptions
from apos.models import TaskSpec


class KernelTests(unittest.TestCase):
    def test_runs_patch_test_loop_without_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import greet


                    class GreetingTests(unittest.TestCase):
                        def test_greet(self):
                            self.assertEqual(greet("APOS"), "Hello, APOS!")


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "fake_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import sys
                        sys.stdin.read()
                        print('''diff --git a/app.py b/app.py
                        --- a/app.py
                        +++ b/app.py
                        @@ -1,2 +1,2 @@
                         def greet(name):
                        -    return name
                        +    return f"Hello, {name}!"
                        '''.replace("                        ", ""), end="")
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )

                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-001",
                        "title": "Greeting",
                        "goal": "Make greet return a friendly message.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        allow_dirty=False,
                        command_timeout_seconds=30,
                    ),
                )

                self.assertEqual(summary.status, "PASS", summary.to_dict())
                self.assertIn('return f"Hello, {name}!"', (root / "app.py").read_text(encoding="utf-8"))
                self.assertIsNotNone(summary.run_log)

                run_log = root / str(summary.run_log)
                self.assertTrue((run_log / "run.json").exists())
                self.assertTrue((run_log / "task.json").exists())
                self.assertTrue((run_log / "attempt-01" / "prompt.json").exists())
                self.assertTrue((run_log / "attempt-01" / "response.patch").exists())
                self.assertTrue((run_log / "attempt-01" / "tests.json").exists())
                self.assertTrue((run_log / "summary.json").exists())

                summary_data = json.loads((run_log / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary_data["status"], "PASS")
                self.assertEqual(summary_data["run_log"], summary.run_log)

                status = self._git_status(root)
                self.assertNotIn(".apos/runs", status)

    def test_rolls_back_failed_attempt_before_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import greet


                    class GreetingTests(unittest.TestCase):
                        def test_greet(self):
                            self.assertEqual(greet("APOS"), "Hello, APOS!")


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "retry_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import json
                        import sys

                        prompt = json.loads(sys.stdin.read())
                        if prompt["attempt"] == 1:
                            replacement = '    return "Wrong"'
                        else:
                            replacement = '    return f"Hello, {name}!"'

                        print(f'''diff --git a/app.py b/app.py
                        --- a/app.py
                        +++ b/app.py
                        @@ -1,2 +1,2 @@
                         def greet(name):
                        -    return name
                        +{replacement}
                        '''.replace("                        ", ""), end="")
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )

                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-ROLLBACK",
                        "title": "Rollback",
                        "goal": "Make greet return a friendly message.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                        "max_attempts": 2,
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        command_timeout_seconds=30,
                    ),
                )

                self.assertEqual(summary.status, "PASS", summary.to_dict())
                self.assertEqual([attempt.status for attempt in summary.attempts], ["FAILED", "PASS"])
                self.assertIn('return f"Hello, {name}!"', (root / "app.py").read_text(encoding="utf-8"))

                run_log = root / str(summary.run_log)
                rollback = json.loads((run_log / "attempt-01" / "rollback.json").read_text(encoding="utf-8"))
                self.assertEqual(rollback["status"], "PASS")

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> None:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)

    @staticmethod
    def _git_status(cwd: Path) -> str:
        completed = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
