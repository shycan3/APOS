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
from apos.git import GitAmbiguousStateError, GitClient


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

    def test_production_run_migrates_patch_apply_without_legacy_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            test_file = root / "test_ok.py"
            test_file.write_text(
                "from app import answer\nassert answer() == 42\nprint('ok')\n",
                encoding="utf-8",
            )
            task = self._write_taskspec(root, branch="apos/patch-apply", allowed_files=["app.py"])
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
            self._git(root, "commit", "-m", "task")

            with (
                patch("apos.cli.Path.cwd", return_value=root),
                patch.object(GitClient, "apply_patch", side_effect=AssertionError("legacy apply called")),
                redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--coder-command",
                        subprocess.list2cmdline([sys.executable, str(coder)]),
                        "--max-attempts",
                        "1",
                        "--no-commit",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("return 42", (root / "app.py").read_text(encoding="utf-8"))
            self.assertIn((Capability.GIT_WORKTREE_WRITE.value, TaskState.SUCCEEDED.value), self._task_rows(root))
            patch_events = [
                event for event in self._audit_events(root)
                if event["capability"] == Capability.GIT_WORKTREE_WRITE.value
                and event["operation"] == "git.run"
            ]
            self.assertEqual([event["status"] for event in patch_events[-4:]], ["REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED"])

    def test_production_run_migrates_rollback_and_allows_retry_after_verified_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            (root / "test_ok.py").write_text(
                "from app import answer\nassert answer() == 42\nprint('ok')\n",
                encoding="utf-8",
            )
            task = self._write_taskspec(root, branch="apos/patch-rollback", allowed_files=["app.py"])
            coder = self._write_sequential_patch_coder(
                root,
                first="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 41
""",
                second="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
""",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "task")

            with (
                patch("apos.cli.Path.cwd", return_value=root),
                patch.object(GitClient, "apply_patch", side_effect=AssertionError("legacy apply called")),
                patch.object(GitClient, "reverse_patch", side_effect=AssertionError("legacy reverse called")),
                redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--coder-command",
                        subprocess.list2cmdline([sys.executable, str(coder)]),
                        "--max-attempts",
                        "2",
                        "--no-commit",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("return 42", (root / "app.py").read_text(encoding="utf-8"))
            rows = self._task_rows(root)
            self.assertIn((Capability.GIT_WORKTREE_WRITE.value, TaskState.SUCCEEDED.value), rows)
            self.assertIn((Capability.GIT_ROLLBACK.value, TaskState.SUCCEEDED.value), rows)

    def test_patch_request_digest_changes_with_patch_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            runtime = ProjectRuntime.create_local_git_phase_a(root)
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.git_execution.bind(actor=actor, approved_by=actor)
            request_a = session._request(
                ("apply", "--recount", "--ignore-space-change", "-"),
                capability=Capability.GIT_WORKTREE_WRITE,
                request_id="patch-request",
                task_id="patch-task",
                stdin_text="diff --git a/a b/a\n",
            )
            request_b = session._request(
                ("apply", "--recount", "--ignore-space-change", "-"),
                capability=Capability.GIT_WORKTREE_WRITE,
                request_id="patch-request",
                task_id="patch-task",
                stdin_text="diff --git a/b b/b\n",
            )

            prepared_a = runtime.execution.prepare(request_a).permission_request
            prepared_b = runtime.execution.prepare(request_b).permission_request

            self.assertNotEqual(prepared_a.metadata["stdin_digest"], prepared_b.metadata["stdin_digest"])
            self.assertNotEqual(prepared_a.digest(), prepared_b.digest())

    def test_patch_check_uses_git_read_without_mutation_or_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            session = self._session(root)
            patch_text = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
"""

            session.check_patch(patch_text)

            self.assertIn("return 0", (root / "app.py").read_text(encoding="utf-8"))
            self.assertEqual(self._task_rows(root), [])
            git_read_events = [
                event for event in self._audit_events(root)
                if event["capability"] == Capability.GIT_READ.value
                and event["operation"] == "git.run"
                and event["status"] == "COMPLETED"
            ]
            self.assertTrue(git_read_events)

    def test_denied_patch_apply_capability_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {
                        Capability.GIT_READ: Decision.ALLOW,
                        Capability.GIT_WORKTREE_WRITE: Decision.DENY,
                    },
                    policy_id="deny-patch-apply",
                ),
                command_policy=CommandPolicy.current_git(),
            )
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.git_execution.bind(actor=actor, approved_by=actor)
            patch_text = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
"""

            with self.assertRaises(GitExecutionError):
                session.apply_patch(patch_text)

            self.assertIn("return 0", (root / "app.py").read_text(encoding="utf-8"))
            self.assertIn((Capability.GIT_WORKTREE_WRITE.value, TaskState.FAILED.value), self._task_rows(root))
            events = [
                event for event in self._audit_events(root)
                if event["capability"] == Capability.GIT_WORKTREE_WRITE.value
                and event["operation"] == "git.run"
            ]
            self.assertEqual([event["status"] for event in events], ["REQUESTED", "DENIED"])

    def test_apply_verification_failure_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            session = self._session(root)
            patch_text = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
"""
            before = session._snapshot(
                patch_digest=session._patch_digest(patch_text),
                expected_paths=session._patch_paths(patch_text),
            )

            with patch.object(session, "_snapshot", side_effect=[before, RuntimeError("lost state")]):
                with self.assertRaises(GitExecutionError) as raised:
                    session.apply_patch(patch_text)

            self.assertTrue(raised.exception.recovery_required)
            self.assertIn(
                (Capability.GIT_WORKTREE_WRITE.value, TaskState.RECOVERY_REQUIRED.value),
                self._task_rows(root),
            )

    def test_ambiguous_rollback_blocks_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            (root / "test_ok.py").write_text(
                "from app import answer\nassert answer() == 42\nprint('ok')\n",
                encoding="utf-8",
            )
            task = self._write_taskspec(root, branch="apos/rollback-recovery", allowed_files=["app.py"])
            coder = self._write_sequential_patch_coder(
                root,
                first="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 41
""",
                second="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def answer():
-    return 0
+    return 42
""",
            )
            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "task")
            stdout = io.StringIO()

            with (
                patch("apos.cli.Path.cwd", return_value=root),
                patch(
                    "apos.controlled_git.ControlledGitClient.reverse_patch",
                    side_effect=GitAmbiguousStateError("simulated ambiguous rollback"),
                ),
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--coder-command",
                        subprocess.list2cmdline([sys.executable, str(coder)]),
                        "--max-attempts",
                        "2",
                        "--no-commit",
                        "--json",
                    ]
                )

            summary = json.loads(stdout.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(summary["status"], "RECOVERY_REQUIRED")
            self.assertEqual(len(summary["attempts"]), 1)
            self.assertEqual(summary["attempts"][0]["status"], "RECOVERY_REQUIRED")

    def test_production_run_migrates_commit_without_legacy_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            (root / "test_ok.py").write_text(
                "from app import answer\nassert answer() == 42\nprint('ok')\n",
                encoding="utf-8",
            )
            task = self._write_taskspec(root, branch="apos/commit-controlled", allowed_files=["app.py"])
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
            self._git(root, "commit", "-m", "task")
            direct_git_calls: list[tuple[str, ...]] = []
            original_run = GitClient.run

            def spy_run(client, args, input_text=None, check=True):
                direct_git_calls.append(tuple(args))
                return original_run(client, args, input_text=input_text, check=check)

            with (
                patch("apos.cli.Path.cwd", return_value=root),
                patch.object(GitClient, "commit", side_effect=AssertionError("legacy commit called")),
                patch.object(GitClient, "run", spy_run),
                redirect_stdout(io.StringIO()),
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--coder-command",
                        subprocess.list2cmdline([sys.executable, str(coder)]),
                        "--max-attempts",
                        "1",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertNotIn(("add", "--", "app.py"), direct_git_calls)
            self.assertNotIn(("commit", "-m", "APOS TASK-GIT-PHASE-A: Git Phase A"), direct_git_calls)
            self.assertNotIn(("rev-parse", "--short", "HEAD"), direct_git_calls)
            rows = self._task_rows(root)
            self.assertIn((Capability.GIT_INDEX_WRITE.value, TaskState.SUCCEEDED.value), rows)
            self.assertIn((Capability.GIT_REF_WRITE.value, TaskState.SUCCEEDED.value), rows)
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "APOS TASK-GIT-PHASE-A: Git Phase A")

    def test_commit_requires_index_and_ref_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            actor = Actor(ActorKind.USER, "local-cli")

            index_runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {
                        Capability.GIT_READ: Decision.ALLOW,
                        Capability.GIT_INDEX_WRITE: Decision.DENY,
                        Capability.GIT_REF_WRITE: Decision.APPROVAL_REQUIRED,
                    },
                    policy_id="deny-index-write",
                ),
                command_policy=CommandPolicy.current_git(),
            )
            with self.assertRaises(GitExecutionError):
                index_runtime.git_execution.bind(actor=actor, approved_by=actor).commit(["test_ok.py"], "commit")
            self.assertEqual(self._git(root, "diff", "--cached", "--name-only").stdout.strip(), "")

            ref_runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {
                        Capability.GIT_READ: Decision.ALLOW,
                        Capability.GIT_INDEX_WRITE: Decision.APPROVAL_REQUIRED,
                        Capability.GIT_REF_WRITE: Decision.DENY,
                    },
                    policy_id="deny-ref-write",
                ),
                command_policy=CommandPolicy.current_git(),
            )
            with self.assertRaises(GitExecutionError):
                ref_runtime.git_execution.bind(actor=actor, approved_by=actor).commit(["test_ok.py"], "commit")
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "initial")

    def test_commit_requires_human_approval_before_index_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            actor = Actor(ActorKind.USER, "local-cli")
            session = ProjectRuntime.create_local_git_phase_a(root).git_execution.bind(actor=actor, approved_by=None)

            with self.assertRaises(GitExecutionError):
                session.commit(["test_ok.py"], "commit")

            self.assertEqual(self._git(root, "diff", "--cached", "--name-only").stdout.strip(), "")
            self.assertIn((Capability.GIT_INDEX_WRITE.value, TaskState.WAITING_APPROVAL.value), self._task_rows(root))

    def test_commit_semantic_binding_changes_request_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            runtime = ProjectRuntime.create_local_git_phase_a(root)
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.git_execution.bind(actor=actor, approved_by=actor)
            base = {
                "git_operation": "commit_task_changes",
                "branch": "master",
                "parent_head": "abc",
                "expected_changed_paths": ["test_ok.py"],
                "staged_diff_digest": "diff-a",
                "index_tree": "tree-a",
                "commit_message_digest": "message-a",
                "task_id": "TASK",
                "attempt_number": 1,
            }
            request_a = session._request(
                ("commit", "--no-verify", "-m", "message"),
                capability=Capability.GIT_REF_WRITE,
                request_id="commit-request",
                task_id="commit-task",
                semantic_metadata=base,
            )
            changed_staged = dict(base, staged_diff_digest="diff-b")
            request_b = session._request(
                ("commit", "--no-verify", "-m", "message"),
                capability=Capability.GIT_REF_WRITE,
                request_id="commit-request",
                task_id="commit-task",
                semantic_metadata=changed_staged,
            )
            changed_message = dict(base, commit_message_digest="message-b")
            request_c = session._request(
                ("commit", "--no-verify", "-m", "message"),
                capability=Capability.GIT_REF_WRITE,
                request_id="commit-request",
                task_id="commit-task",
                semantic_metadata=changed_message,
            )

            prepared_a = runtime.execution.prepare(request_a).permission_request
            prepared_b = runtime.execution.prepare(request_b).permission_request
            prepared_c = runtime.execution.prepare(request_c).permission_request

            self.assertNotEqual(prepared_a.metadata["semantic_digest"], prepared_b.metadata["semantic_digest"])
            self.assertNotEqual(prepared_a.metadata["semantic_digest"], prepared_c.metadata["semantic_digest"])
            self.assertNotEqual(prepared_a.digest(), prepared_b.digest())
            self.assertNotEqual(prepared_a.digest(), prepared_c.digest())

    def test_commit_guard_blocks_staged_diff_drift_before_process_start(self):
        def drift(root: Path) -> None:
            (root / "test_ok.py").write_text("print('drifted')\n", encoding="utf-8")
            self._git(root, "add", "test_ok.py")

        self._assert_commit_guard_blocks_drift(drift)

    def test_commit_guard_blocks_index_tree_drift_before_process_start(self):
        def drift(root: Path) -> None:
            (root / "test_ok.py").write_text("print('tree drift')\n", encoding="utf-8")
            self._git(root, "add", "test_ok.py")

        self._assert_commit_guard_blocks_drift(drift)

    def test_commit_guard_blocks_staged_file_drift_before_process_start(self):
        def drift(root: Path) -> None:
            (root / "extra.py").write_text("print('extra')\n", encoding="utf-8")
            self._git(root, "add", "extra.py")

        self._assert_commit_guard_blocks_drift(drift)

    def test_commit_guard_blocks_parent_head_drift_before_process_start(self):
        def drift(root: Path) -> None:
            tree = self._git(root, "write-tree").stdout.strip()
            created = subprocess.run(
                ["git", "commit-tree", tree, "-p", "HEAD"],
                cwd=root,
                input="drift parent\n",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            self._git(root, "update-ref", "HEAD", created.stdout.strip())

        self._assert_commit_guard_blocks_drift(drift)

    def test_commit_guard_blocks_branch_drift_before_process_start(self):
        def drift(root: Path) -> None:
            self._git(root, "checkout", "-b", "drift-branch")

        self._assert_commit_guard_blocks_drift(drift)

    def test_commit_guard_allows_matching_state_to_start_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            commit_started: list[tuple[str, ...]] = []
            original_popen = subprocess.Popen

            def spy_popen(args, *popen_args, **popen_kwargs):
                if isinstance(args, (list, tuple)) and "commit" in args:
                    commit_started.append(tuple(str(arg) for arg in args))
                return original_popen(args, *popen_args, **popen_kwargs)

            with patch("subprocess.Popen", side_effect=spy_popen):
                session.commit(["test_ok.py"], "commit")

            self.assertTrue(commit_started)
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "commit")

    def test_commit_blocks_preexisting_staged_changes_with_allow_dirty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root, content="def answer():\n    return 0\n")
            (root / "test_ok.py").write_text(
                "from app import answer\nassert answer() == 42\nprint('ok')\n",
                encoding="utf-8",
            )
            (root / "unrelated.txt").write_text("user staged\n", encoding="utf-8")
            self._git(root, "add", "unrelated.txt")
            task = self._write_taskspec(root, branch="apos/staged-blocked", allowed_files=["app.py"])
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

            stdout = io.StringIO()
            with (
                patch("apos.cli.Path.cwd", return_value=root),
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "run",
                        str(task),
                        "--coder-command",
                        subprocess.list2cmdline([sys.executable, str(coder)]),
                        "--max-attempts",
                        "1",
                        "--allow-dirty",
                        "--json",
                    ]
                )

            summary = json.loads(stdout.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(summary["status"], "FAILED")
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "initial")
            self.assertEqual(self._git(root, "diff", "--cached", "--name-only").stdout.strip(), "unrelated.txt")

    def test_cached_diff_exit_codes_are_distinguished(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            original_run_read = session._run_read

            with patch.object(session, "_run_read", wraps=original_run_read) as spy:
                session.commit(["test_ok.py"], "commit")

            diff_calls = [call.args for call in spy.call_args_list if call.args[:3] == ("diff", "--cached", "--quiet")]
            self.assertTrue(diff_calls)
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "commit")

            (root / "test_ok.py").write_text("print('changed again')\n", encoding="utf-8")

            def fake_diff_error(*args, **kwargs):
                if args[:3] == ("diff", "--cached", "--quiet"):
                    return ToolResult.fail(ErrorCode.PROCESS_EXIT_NONZERO, "process exited with code 128", details={"exit_code": 128})
                return original_run_read(*args, **kwargs)

            from apos.core.result import ErrorCode, ToolResult

            with patch.object(session, "_run_read", side_effect=fake_diff_error):
                with self.assertRaises(GitExecutionError) as raised:
                    session.commit(["test_ok.py"], "commit 2")
            self.assertFalse(raised.exception.recovery_required)
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "commit")

    def test_git_add_partial_mutation_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            original_run_git_task = session._run_git_task

            def fake_add_failure(*args, **kwargs):
                if args and args[0][:1] == ("add",):
                    self._git(root, "add", "test_ok.py")
                    from apos.core.result import ErrorCode, ToolResult

                    return ToolResult.fail(ErrorCode.PROCESS_EXIT_NONZERO, "process exited with code 128", details={"exit_code": 128})
                return original_run_git_task(*args, **kwargs)

            with patch.object(session, "_run_git_task", side_effect=fake_add_failure):
                with self.assertRaises(GitExecutionError) as raised:
                    session.commit(["test_ok.py"], "commit")

            self.assertTrue(raised.exception.recovery_required)

    def test_commit_failure_with_changed_head_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            original_run_git_task = session._run_git_task

            def fake_commit_failure(*args, **kwargs):
                if args and args[0][:1] == ("commit",):
                    self._git(root, "commit", "-m", "created despite failure")
                    from apos.core.result import ErrorCode, ToolResult

                    return ToolResult.fail(ErrorCode.PROCESS_EXIT_NONZERO, "process exited with code 1", details={"exit_code": 1})
                return original_run_git_task(*args, **kwargs)

            with patch.object(session, "_run_git_task", side_effect=fake_commit_failure):
                with self.assertRaises(GitExecutionError) as raised:
                    session.commit(["test_ok.py"], "commit")

            self.assertTrue(raised.exception.recovery_required)

    def test_commit_post_verification_checks_parent_tree_message_and_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")

            with patch.object(session, "_commit_message", return_value="tampered"):
                with self.assertRaises(GitExecutionError) as raised:
                    session.commit(["test_ok.py"], "commit")

            self.assertTrue(raised.exception.recovery_required)

    def test_commit_finalization_failure_after_created_commit_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            original_complete = session._complete_git_task

            def fail_after_commit(task_id, succeeded, result):
                if task_id.startswith("git-commit:") and succeeded:
                    from apos.core.result import ErrorCode
                    from apos.core.tasks import TaskError

                    raise TaskError(ErrorCode.PERSISTENCE_CORRUPTED, "simulated persistence failure")
                return original_complete(task_id, succeeded, result)

            with patch.object(session, "_complete_git_task", side_effect=fail_after_commit):
                with self.assertRaises(GitExecutionError) as raised:
                    session.commit(["test_ok.py"], "commit")

            self.assertTrue(raised.exception.recovery_required)
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "commit")

    def test_commit_suppresses_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            hook = root / ".git" / "hooks" / "pre-commit"
            marker = root / "hook-ran.txt"
            hook.write_text(f"#!/bin/sh\nprintf bad > {marker.as_posix()!r}\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")

            self._session(root).commit(["test_ok.py"], "commit")

            self.assertFalse(marker.exists())
            self.assertEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "commit")

    def _session(self, root: Path):
        runtime = ProjectRuntime.create_local_git_phase_a(root)
        actor = Actor(ActorKind.USER, "local-cli")
        return runtime.git_execution.bind(actor=actor, approved_by=actor)

    def _init_repo(self, root: Path, *, content: str = "print('ok')\n") -> None:
        self._git(root, "init", "-b", "master")
        self._git(root, "config", "user.email", "apos@example.test")
        self._git(root, "config", "user.name", "APOS Test")
        if content.startswith("def answer"):
            (root / "app.py").write_text(content, encoding="utf-8")
        else:
            (root / "test_ok.py").write_text(content, encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-m", "initial")

    def _write_taskspec(
        self,
        root: Path,
        *,
        branch: str,
        allowed_files: list[str] | None = None,
    ) -> Path:
        task = root / "task.json"
        task.write_text(
            json.dumps(
                {
                    "task_id": "TASK-GIT-PHASE-A",
                    "title": "Git Phase A",
                    "goal": "Exercise migrated Git branch preparation.",
                    "branch": branch,
                    "allowed_files": allowed_files or ["test_ok.py"],
                    "test_commands": [subprocess.list2cmdline([sys.executable, "test_ok.py"])],
                }
            ),
            encoding="utf-8",
        )
        return task

    def _write_patch_coder(self, root: Path, patch_text: str) -> Path:
        coder = root / "fake_coder.py"
        coder.write_text(
            "import sys\n"
            "sys.stdin.read()\n"
            f"print({patch_text!r}, end='')\n",
            encoding="utf-8",
        )
        return coder

    def _write_sequential_patch_coder(self, root: Path, *, first: str, second: str) -> Path:
        coder = root / "fake_coder.py"
        patches = {1: first, 2: second}
        coder.write_text(
            "import json, sys\n"
            "payload = json.loads(sys.stdin.read())\n"
            f"patches = {patches!r}\n"
            "print(patches[payload['attempt']], end='')\n",
            encoding="utf-8",
        )
        return coder

    def _assert_commit_guard_blocks_drift(self, drift) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)
            session = self._session(root)
            (root / "test_ok.py").write_text("print('changed')\n", encoding="utf-8")
            original_factory = session._commit_drift_guard
            commit_started: list[tuple[str, ...]] = []
            original_popen = subprocess.Popen

            def guard_factory(approved, expected_paths):
                approved_guard = original_factory(approved, expected_paths)

                def guard() -> None:
                    drift(root)
                    approved_guard()

                return guard

            def spy_popen(args, *popen_args, **popen_kwargs):
                if isinstance(args, (list, tuple)) and "commit" in args:
                    commit_started.append(tuple(str(arg) for arg in args))
                return original_popen(args, *popen_args, **popen_kwargs)

            with (
                patch.object(session, "_commit_drift_guard", side_effect=guard_factory),
                patch("subprocess.Popen", side_effect=spy_popen),
            ):
                with self.assertRaises(GitExecutionError) as raised:
                    session.commit(["test_ok.py"], "commit")

            self.assertFalse(raised.exception.recovery_required)
            self.assertFalse(commit_started)
            self.assertNotEqual(self._git(root, "log", "-1", "--format=%s").stdout.strip(), "commit")

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
