import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import patch

from apos.cli import main
from apos.core import (
    Actor,
    ActorKind,
    Capability,
    CommandPolicy,
    Decision,
    GitSnapshot,
    ProjectRuntime,
    StaticPermissionPolicy,
    TaskState,
)
from apos.core.git_execution import GitExecutionError
from apos.git import GitClient


class ProductionGitControlPlaneTests(unittest.TestCase):
    def test_production_run_migrates_git_read_and_branch_prep(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            task = self._write_taskspec(root, branch="apos/test-controlled-git")
            self._git(root, "add", "task.json")
            self._git(root, "commit", "-m", "task")
            direct_git_calls: list[tuple[str, ...]] = []
            original = GitClient.run

            def spy(client, args, input_text=None, check=True):
                direct_git_calls.append(tuple(args))
                return original(client, args, input_text=input_text, check=check)

            stdout = io.StringIO()
            with (
                patch("apos.cli.Path.cwd", return_value=root),
                patch.object(GitClient, "run", spy),
                redirect_stdout(stdout),
            ):
                code = main(["run", str(task), "--no-commit"])

            self.assertEqual(code, 0, stdout.getvalue())
            self.assertEqual(self._git(root, "branch", "--show-current").stdout.strip(), "apos/test-controlled-git")
            self.assertNotIn(("rev-parse", "--show-toplevel"), direct_git_calls)
            self.assertNotIn(("status", "--porcelain"), direct_git_calls)
            self.assertNotIn(("checkout", "-b", "apos/test-controlled-git"), direct_git_calls)

            capabilities = [event["capability"] for event in self._audit_events(root)]
            self.assertIn(Capability.GIT_READ.value, capabilities)
            self.assertIn(Capability.GIT_WORKTREE_WRITE.value, capabilities)
            self.assertIn(Capability.TEST_EXECUTE.value, capabilities)
            task_rows = self._task_rows(root)
            self.assertIn((Capability.GIT_WORKTREE_WRITE.value, TaskState.SUCCEEDED.value), task_rows)
            self.assertIn((Capability.TEST_EXECUTE.value, TaskState.SUCCEEDED.value), task_rows)

    def test_git_read_ignores_host_git_environment(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = Path(tmp)
            outside = Path(outside_tmp)
            self._init_repo(root)
            self._init_repo(outside)
            self._git(outside, "checkout", "-b", "outside-env")
            session = self._session(root)
            previous_git_dir = os.environ.get("GIT_DIR")
            previous_work_tree = os.environ.get("GIT_WORK_TREE")
            os.environ["GIT_DIR"] = str(outside / ".git")
            os.environ["GIT_WORK_TREE"] = str(outside)
            try:
                self.assertNotEqual(session.current_branch(), "outside-env")
                self.assertNotIn("outside-env", session.status_porcelain())
            finally:
                self._restore_env("GIT_DIR", previous_git_dir)
                self._restore_env("GIT_WORK_TREE", previous_work_tree)

    def test_invalid_branch_name_is_rejected_before_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)

            with self.assertRaises(GitExecutionError):
                session.checkout_task_branch("-c")
            with self.assertRaises(GitExecutionError):
                session.checkout_task_branch("@{-1}")

            self.assertEqual(self._git(root, "branch", "--show-current").stdout.strip(), "master")
            self.assertNotIn(Capability.GIT_WORKTREE_WRITE.value, [event["capability"] for event in self._audit_events(root)])

    def test_denied_git_worktree_capability_fails_without_process_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {
                        Capability.GIT_READ: Decision.ALLOW,
                        Capability.GIT_WORKTREE_WRITE: Decision.DENY,
                    },
                    policy_id="deny-git-worktree-write",
                ),
                command_policy=CommandPolicy.current_git(),
            )
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.git_execution.bind(actor=actor, approved_by=actor)

            with self.assertRaises(GitExecutionError):
                session.checkout_task_branch("apos/denied")

            self.assertEqual(self._git(root, "branch", "--show-current").stdout.strip(), "master")
            task_rows = self._task_rows(root)
            self.assertIn((Capability.GIT_WORKTREE_WRITE.value, TaskState.FAILED.value), task_rows)
            events = [
                event
                for event in self._audit_events(root)
                if event["capability"] == Capability.GIT_WORKTREE_WRITE.value
                and event["operation"] == "git.run"
            ]
            self.assertEqual([event["status"] for event in events], ["REQUESTED", "DENIED"])

    def test_git_branch_prep_requires_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            runtime = ProjectRuntime.create_local_git_phase_a(root)
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.git_execution.bind(actor=actor, approved_by=None)

            with self.assertRaises(GitExecutionError):
                session.checkout_task_branch("apos/no-approval")

            self.assertEqual(self._git(root, "branch", "--show-current").stdout.strip(), "master")
            self.assertIn((Capability.GIT_WORKTREE_WRITE.value, TaskState.WAITING_APPROVAL.value), self._task_rows(root))

    def test_git_branch_verification_failure_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            before = GitSnapshot(branch="master", head=self._git(root, "rev-parse", "HEAD").stdout.strip(), dirty=False)

            with patch.object(session, "_snapshot", side_effect=[before, RuntimeError("lost state")]):
                with self.assertRaises(GitExecutionError):
                    session.checkout_task_branch("apos/recovery")

            self.assertIn(
                (Capability.GIT_WORKTREE_WRITE.value, TaskState.RECOVERY_REQUIRED.value),
                self._task_rows(root),
            )

    def test_git_checkout_hook_is_disabled_for_branch_prep(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            hook = root / ".git" / "hooks" / "post-checkout"
            marker = root / "hook-ran.txt"
            hook.write_text(f"#!/bin/sh\nprintf bad > {marker.as_posix()!r}\n", encoding="utf-8")
            hook.chmod(0o755)

            self._session(root).checkout_task_branch("apos/no-hook")

            self.assertFalse(marker.exists())
            self.assertEqual(self._git(root, "branch", "--show-current").stdout.strip(), "apos/no-hook")

    def _session(self, root: Path):
        runtime = ProjectRuntime.create_local_git_phase_a(root)
        actor = Actor(ActorKind.USER, "local-cli")
        return runtime.git_execution.bind(actor=actor, approved_by=actor)

    def _init_repo(self, root: Path) -> None:
        self._git(root, "init", "-b", "master")
        self._git(root, "config", "user.email", "apos@example.test")
        self._git(root, "config", "user.name", "APOS Test")
        (root / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "initial")

    def _write_taskspec(self, root: Path, *, branch: str) -> Path:
        task = root / "task.json"
        task.write_text(
            json.dumps(
                {
                    "task_id": "TASK-GIT-PHASE-A",
                    "title": "Git Phase A",
                    "goal": "Exercise migrated Git branch preparation.",
                    "branch": branch,
                    "allowed_files": ["test_ok.py"],
                    "test_commands": [subprocess.list2cmdline([sys.executable, "test_ok.py"])],
                }
            ),
            encoding="utf-8",
        )
        return task

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

    def _audit_events(self, root: Path) -> list[dict[str, object]]:
        path = root / ".apos" / "audit" / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

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

    @staticmethod
    def _restore_env(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
