import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from apos.application import APOSApplicationService, APOSStatus
from apos.core import Capability, TaskState
from apos.executor import run_commands
from apos.git import GitAmbiguousStateError, GitClient
from apos.kernel import RunOptions
from apos.models import RunSummary, SpecError, TaskSpec


class ApplicationServiceTests(unittest.TestCase):
    def test_validate_task_returns_structured_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = self._write_taskspec(root)
            invalid = root / "invalid.json"
            invalid.write_text(json.dumps({"task_id": "TASK-BAD"}), encoding="utf-8")
            service = APOSApplicationService(root)

            valid_result = service.validate_task(valid.name)
            invalid_result = service.validate_task(invalid.name)

            self.assertTrue(valid_result.success)
            self.assertEqual(valid_result.data["task_id"], "TASK-APP")
            self.assertIn("taskspec", valid_result.data)
            self.assertFalse(invalid_result.success)
            self.assertIn("goal is required", invalid_result.error.message)

    def test_run_task_file_owns_controlled_git_and_test_wiring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            task = self._write_taskspec(root, branch="apos/app-service")
            coder = self._write_patch_coder(
                root,
                """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
""",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            service = APOSApplicationService(root)

            with (
                patch.object(GitClient, "apply_patch", side_effect=AssertionError("legacy apply called")),
                patch.object(GitClient, "reverse_patch", side_effect=AssertionError("legacy reverse called")),
                patch.object(GitClient, "commit", side_effect=AssertionError("legacy commit called")),
                patch("apos.kernel.run_commands", side_effect=AssertionError("legacy test runner called")),
                patch("apos.executor.run_commands", side_effect=AssertionError("legacy executor called")),
            ):
                summary = service.run_task_file(
                    task.name,
                    RunOptions(
                        coder_command=subprocess.list2cmdline([sys.executable, str(coder)]),
                        max_attempts=1,
                        command_timeout_seconds=30,
                    ),
                )

            self.assertIsInstance(summary, RunSummary)
            self.assertEqual(summary.status, "PASS")
            self.assertTrue(summary.committed)
            rows = self._task_rows(root)
            self.assertIn((Capability.TEST_EXECUTE.value, TaskState.SUCCEEDED.value), rows)
            self.assertIn((Capability.GIT_INDEX_WRITE.value, TaskState.SUCCEEDED.value), rows)
            self.assertIn((Capability.GIT_REF_WRITE.value, TaskState.SUCCEEDED.value), rows)
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "APOS TASK-APP: Application service")

    def test_run_task_accepts_taskspec_without_manual_kernel_factories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 42\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            spec = TaskSpec.from_mapping(
                {
                    "task_id": "TASK-DIRECT",
                    "title": "Direct service",
                    "goal": "Use service directly.",
                    "branch": "apos/direct-service",
                    "allowed_files": ["app.py"],
                    "test_commands": [self._test_command()],
                }
            )

            summary = APOSApplicationService(root).run_task(spec, RunOptions(no_commit=True, command_timeout_seconds=30))

            self.assertEqual(summary.status, "PASS")
            self.assertFalse(summary.committed)
            self.assertEqual(self._git(root, "branch", "--show-current").stdout.strip(), "apos/direct-service")

    def test_run_task_validates_direct_taskspec_before_kernel_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = TaskSpec(task_id="TASK-BAD", goal="Invalid direct spec")

            with patch("apos.application.Kernel", side_effect=AssertionError("Kernel should not be reached")) as kernel:
                with self.assertRaisesRegex(SpecError, "allowed_files must contain at least one path"):
                    APOSApplicationService(root).run_task(spec)

            kernel.assert_not_called()

    def test_no_commit_option_skips_git_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            task = self._write_taskspec(root, branch="apos/no-commit")
            coder = self._write_patch_coder(
                root,
                """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
""",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")

            summary = APOSApplicationService(root).run_task_file(
                task.name,
                RunOptions(
                    coder_command=subprocess.list2cmdline([sys.executable, str(coder)]),
                    max_attempts=1,
                    no_commit=True,
                    command_timeout_seconds=30,
                ),
            )

            self.assertEqual(summary.status, "PASS")
            self.assertFalse(summary.committed)
            self.assertNotIn(Capability.GIT_INDEX_WRITE.value, [row[0] for row in self._task_rows(root)])
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "initial")

    def test_recovery_required_propagates_through_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            task = self._write_taskspec(root, branch="apos/recovery")
            coder = self._write_patch_coder(
                root,
                """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
""",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")

            with patch("apos.controlled_git.ControlledGitClient.apply_patch", side_effect=GitAmbiguousStateError("ambiguous")):
                summary = APOSApplicationService(root).run_task_file(
                    task.name,
                    RunOptions(
                        coder_command=subprocess.list2cmdline([sys.executable, str(coder)]),
                        max_attempts=1,
                        command_timeout_seconds=30,
                    ),
                )

            self.assertEqual(summary.status, "RECOVERY_REQUIRED")

    def test_status_and_run_inspection_are_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 42\n")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "initial")
            task = self._write_taskspec(root, branch="apos/run-inspection")
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "task")
            service = APOSApplicationService(root)

            status = service.get_status()
            summary = service.run_task_file(task.name, RunOptions(no_commit=True, command_timeout_seconds=30))
            entries = service.list_runs()
            detail = service.get_run(summary.run_log)

            self.assertIsInstance(status, APOSStatus)
            self.assertEqual(status.branch, "master")
            self.assertFalse(status.dirty)
            self.assertEqual(status.to_dict()["version"], status.version)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].task_id, "TASK-APP")
            self.assertEqual(detail["summary"]["status"], "PASS")

    @staticmethod
    def _write_taskspec(root: Path, *, branch: str = "apos/app-service") -> Path:
        path = root / "task.json"
        path.write_text(
            json.dumps(
                {
                    "task_id": "TASK-APP",
                    "title": "Application service",
                    "goal": "Route through the application service.",
                    "branch": branch,
                    "allowed_files": ["app.py"],
                    "test_commands": [ApplicationServiceTests._test_command()],
                    "max_attempts": 1,
                }
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _write_patch_coder(root: Path, patch_text: str) -> Path:
        coder = root / "fake_coder.py"
        coder.write_text(f"print({patch_text!r}, end='')\n", encoding="utf-8")
        return coder

    @staticmethod
    def _test_command() -> str:
        return f"{sys.executable} test_ok.py"

    def _init_repo(self, root: Path, *, content: str) -> None:
        self._git(root, "init")
        self._git(root, "config", "user.email", "apos@example.test")
        self._git(root, "config", "user.name", "APOS Test")
        (root / "app.py").write_text(content, encoding="utf-8")
        (root / "test_ok.py").write_text(
            "from app import answer\nassert answer() == 42\nprint('ok')\n",
            encoding="utf-8",
        )

    def _git(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr or completed.stdout)
        return completed

    def _task_rows(self, root: Path) -> list[tuple[str, str]]:
        path = root / ".apos" / "state" / "tasks.sqlite3"
        if not path.exists():
            return []
        with closing(sqlite3.connect(path)) as connection:
            return [
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT requested_capability, state FROM tasks ORDER BY created_at, task_id"
                )
            ]


if __name__ == "__main__":
    unittest.main()
