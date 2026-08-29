import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from unittest.mock import patch

from apos.cli import main
from apos.core import (
    Actor,
    ActorKind,
    ApprovalAction,
    ApprovalSource,
    Capability,
    CommandPolicy,
    CommandRequest,
    Decision,
    ErrorCode,
    ProjectRuntime,
    StaticPermissionPolicy,
    TaskState,
    TaskError,
)


class RunTestExecutionControlPlaneTests(unittest.TestCase):
    def test_production_run_uses_persistent_control_plane_without_legacy_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = subprocess.list2cmdline(
                [sys.executable, "-c", "print('modern preflight passed')"]
            )
            taskspec = self._init_project(root, command)

            stdout = io.StringIO()
            with self._production_run_guards(root), redirect_stdout(stdout):
                return_code = main(["run", str(taskspec), "--no-commit"])

            task_rows, approval_rows = self._task_rows(root)
            execution_events = self._execution_events(root)

            self.assertEqual(return_code, 0)
            self.assertIn("통과(PASS)", stdout.getvalue())
            self.assertEqual([row[0] for row in task_rows], [TaskState.SUCCEEDED.value])
            self.assertEqual([row[1] for row in task_rows], [Capability.TEST_EXECUTE.value])
            self.assertEqual(len(approval_rows), 1)
            self.assertIsNotNone(approval_rows[0][0])
            self.assertEqual(
                [event["status"] for event in execution_events],
                ["REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED"],
            )
            self.assertTrue(all(event["capability"] == "TEST_EXECUTE" for event in execution_events))
            self.assertTrue(all(event["actor"] == {"kind": "USER", "actor_id": "local-cli"} for event in execution_events))

    def test_post_change_test_execution_also_uses_modern_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import answer


                    class AppTests(unittest.TestCase):
                        def test_answer(self):
                            self.assertEqual(answer(), 42)
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            command = subprocess.list2cmdline(
                [sys.executable, "-m", "unittest", "test_app.py"]
            )
            taskspec = self._write_taskspec(root, command, allowed_files=["app.py"])
            coder_script = root / "fake_coder.py"
            coder_script.write_text(
                "import json\n"
                "print(json.dumps({"
                "'type': 'file_replacement', "
                "'path': 'app.py', "
                "'content': 'def answer():\\n    return 42\\n'"
                "}))\n",
                encoding="utf-8",
            )
            coder = subprocess.list2cmdline([sys.executable, str(coder_script)])
            self._commit_project(root)

            stdout = io.StringIO()
            with self._production_run_guards(root), redirect_stdout(stdout):
                return_code = main(
                    [
                        "run",
                        str(taskspec),
                        "--coder-command",
                        coder,
                        "--max-attempts",
                        "1",
                        "--no-commit",
                    ]
                )

            task_rows, _ = self._task_rows(root)
            execution_events = self._execution_events(root)
            execution_finishes = [
                event["status"]
                for event in execution_events
                if event["status"] in {"COMPLETED", "FAILED"}
            ]

            self.assertEqual(return_code, 0, stdout.getvalue())
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")
            self.assertEqual([row[0] for row in task_rows], ["FAILED", "SUCCEEDED"])
            self.assertEqual(execution_finishes, ["FAILED", "COMPLETED"])

    def test_denied_test_capability_does_not_start_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "must-not-exist.txt"
            command = subprocess.list2cmdline(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('must-not-exist.txt').write_text('bad')",
                ]
            )
            taskspec = self._init_project(root, command)
            denied_runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {Capability.TEST_EXECUTE: Decision.DENY},
                    policy_id="deny-production-test",
                ),
                command_policy=CommandPolicy.current_python(),
            )

            stderr = io.StringIO()
            with (
                self._production_run_guards(root),
                patch(
                    "apos.application.ProjectRuntime.create_local_test_execution",
                    return_value=denied_runtime,
                ),
                redirect_stderr(stderr),
            ):
                return_code = main(["run", str(taskspec), "--no-commit"])

            task_rows, _ = self._task_rows(root)
            execution_events = self._execution_events(root)

            self.assertEqual(return_code, 1)
            self.assertFalse(marker.exists())
            self.assertIn("no Local Coder command configured", stderr.getvalue())
            self.assertEqual([row[0] for row in task_rows], [TaskState.FAILED.value])
            self.assertEqual(
                [event["status"] for event in execution_events],
                ["REQUESTED", "DENIED"],
            )

    def test_nonzero_test_closes_task_and_execution_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = subprocess.list2cmdline(
                [sys.executable, "-c", "raise SystemExit(7)"]
            )
            taskspec = self._init_project(root, command)

            stderr = io.StringIO()
            with self._production_run_guards(root), redirect_stderr(stderr):
                return_code = main(["run", str(taskspec), "--no-commit"])

            task_rows, _ = self._task_rows(root)
            execution_events = self._execution_events(root)
            failed = execution_events[-1]

            self.assertEqual(return_code, 1)
            self.assertEqual([row[0] for row in task_rows], [TaskState.FAILED.value])
            self.assertEqual(
                [event["status"] for event in execution_events],
                ["REQUESTED", "AUTHORIZED", "STARTED", "FAILED"],
            )
            self.assertEqual(failed["exit_code"], 7)
            self.assertEqual(failed["error_code"], "PROCESS_EXIT_NONZERO")
            self.assertIn("no Local Coder command configured", stderr.getvalue())

    def test_run_runtime_construction_does_not_recover_unrelated_running_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = subprocess.list2cmdline(
                [sys.executable, "-c", "print('unrelated recovery regression')"]
            )
            taskspec = self._init_project(root, command)
            runtime = ProjectRuntime.create_local_test_execution(root)
            task_actor = Actor(ActorKind.EXTERNAL_AI, "unrelated-agent")
            owner = Actor(ActorKind.USER, "local-owner")
            request = CommandRequest(
                executable=sys.executable,
                args=("-c", "print('do not run')"),
                actor=task_actor,
                capability=Capability.TEST_EXECUTE,
                request_id="unrelated-request",
                task_id="unrelated-running",
            )
            runtime.tasks.create_command_task(request, description="Unrelated running task")
            runtime.tasks.queue_task(request.task_id, actor=task_actor)
            runtime.tasks.request_approval(request.task_id, actor=task_actor)
            permission_request = runtime.tasks.get_permission_request(request.task_id)
            approval = runtime.tasks.grant_approval(
                request.task_id,
                action=ApprovalAction(
                    request_id=request.request_id,
                    request_digest=permission_request.digest(),
                    subject=task_actor,
                    approved_by=owner,
                    source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                    note="Prepare recovery regression state.",
                ),
            )
            self.assertTrue(
                runtime.tasks.consume_approval(permission_request, approval.to_grant()).allowed
            )
            self.assertEqual(
                runtime.tasks.get_task(request.task_id).state,
                TaskState.RUNNING,
            )

            with self._production_run_guards(root), redirect_stdout(io.StringIO()):
                return_code = main(["run", str(taskspec), "--no-commit"])

            self.assertEqual(return_code, 0)
            self.assertEqual(
                runtime.tasks.get_task(request.task_id).state,
                TaskState.RUNNING,
            )
            runtime.recover_interrupted_tasks()
            self.assertEqual(
                runtime.tasks.get_task(request.task_id).state,
                TaskState.RECOVERY_REQUIRED,
            )

    def test_test_execution_waits_when_local_approval_is_not_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "approval-required.txt"
            runtime = ProjectRuntime.create_local_test_execution(root)
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.test_execution.bind(actor=actor, approved_by=None)
            command = subprocess.list2cmdline(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('approval-required.txt').write_text('bad')",
                ]
            )

            results = session.run_commands(
                [command],
                cwd=root,
                timeout_seconds=10,
                task_id="TASK-APPROVAL",
            )
            task_rows, approval_rows = self._task_rows(root)

            self.assertEqual(results[0].error_type, "PERMISSION_REQUIRED")
            self.assertFalse(marker.exists())
            self.assertEqual([row[0] for row in task_rows], [TaskState.WAITING_APPROVAL.value])
            self.assertEqual(approval_rows, [])
            self.assertEqual(self._execution_events(root), [])

    def test_task_persistence_failure_cannot_fall_through_to_process_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "persistence-bypass.txt"
            runtime = ProjectRuntime.create_local_test_execution(root)
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.test_execution.bind(actor=actor, approved_by=actor)
            command = subprocess.list2cmdline(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('persistence-bypass.txt').write_text('bad')",
                ]
            )

            with (
                patch.object(
                    runtime.tasks,
                    "create_command_task",
                    side_effect=TaskError(
                        ErrorCode.PERSISTENCE_CORRUPTED,
                        "simulated task persistence failure",
                    ),
                ),
                patch.object(
                    runtime.execution,
                    "run",
                    side_effect=AssertionError("execution bypassed persistent task creation"),
                ),
            ):
                results = session.run_commands(
                    [command],
                    cwd=root,
                    timeout_seconds=10,
                    task_id="TASK-PERSISTENCE-FAILURE",
                )

            self.assertEqual(results[0].error_type, "TASK_PERSISTENCE_FAILED")
            self.assertFalse(marker.exists())

    def test_execution_exception_after_approval_consumption_requires_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ProjectRuntime.create_local_test_execution(root)
            actor = Actor(ActorKind.USER, "local-cli")
            session = runtime.test_execution.bind(actor=actor, approved_by=actor)
            command = subprocess.list2cmdline(
                [sys.executable, "-c", "print('result unknown')"]
            )

            def consume_then_raise(request, *, approval=None, network_approval=None):
                self.assertIsNotNone(approval)
                permission_request = runtime.tasks.get_permission_request(request.task_id)
                consumed = runtime.tasks.consume_approval(permission_request, approval)
                self.assertTrue(consumed.allowed, consumed.reason)
                raise RuntimeError("simulated audit persistence failure")

            with patch.object(runtime.execution, "run", side_effect=consume_then_raise):
                results = session.run_commands(
                    [command],
                    cwd=root,
                    timeout_seconds=10,
                    task_id="TASK-UNKNOWN-RESULT",
                )

            task_rows, approval_rows = self._task_rows(root)
            self.assertEqual(results[0].error_type, ErrorCode.INTERNAL_ERROR.value)
            self.assertEqual([row[0] for row in task_rows], [TaskState.RECOVERY_REQUIRED.value])
            self.assertIsNotNone(approval_rows[0][0])

    @staticmethod
    def _production_run_guards(root: Path):
        return _CombinedPatches(
            patch("apos.cli.Path.cwd", return_value=root),
            patch("apos.kernel.run_commands", side_effect=AssertionError("legacy runner called")),
            patch("apos.executor.run_command", side_effect=AssertionError("legacy executor called")),
        )

    def _init_project(self, root: Path, command: str) -> Path:
        taskspec = self._write_taskspec(root, command)
        self._commit_project(root)
        return taskspec

    @staticmethod
    def _write_taskspec(
        root: Path,
        command: str,
        *,
        allowed_files: list[str] | None = None,
    ) -> Path:
        taskspec = root / "task.json"
        taskspec.write_text(
            json.dumps(
                {
                    "task_id": "TASK-CONTROLLED-TEST",
                    "title": "Controlled production test",
                    "goal": "Run TaskSpec tests through the modern control plane.",
                    "allowed_files": allowed_files or ["task.json"],
                    "test_commands": [command],
                    "max_attempts": 1,
                }
            ),
            encoding="utf-8",
        )
        return taskspec

    @staticmethod
    def _commit_project(root: Path) -> None:
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "apos@example.test"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "APOS Test"],
            cwd=root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=root,
            check=True,
            capture_output=True,
        )

    @staticmethod
    def _task_rows(root: Path):
        with closing(
            sqlite3.connect(root / ".apos" / "state" / "tasks.sqlite3")
        ) as connection:
            tasks = connection.execute(
                """
                SELECT state, requested_capability
                FROM tasks
                WHERE requested_capability = ?
                ORDER BY created_at, task_id
                """,
                (Capability.TEST_EXECUTE.value,),
            ).fetchall()
            approvals = connection.execute(
                """
                SELECT approvals.consumed_at
                FROM approvals
                JOIN tasks ON approvals.task_id = tasks.task_id
                WHERE tasks.requested_capability = ?
                ORDER BY approvals.issued_at, approvals.approval_id
                """,
                (Capability.TEST_EXECUTE.value,),
            ).fetchall()
        return tasks, approvals

    @staticmethod
    def _execution_events(root: Path) -> list[dict[str, object]]:
        path = root / ".apos" / "audit" / "events.jsonl"
        if not path.exists():
            return []
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [event for event in events if event.get("operation") == "test.run"]


class _CombinedPatches:
    def __init__(self, *patchers) -> None:
        self.patchers = patchers

    def __enter__(self):
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        for patcher in reversed(self.patchers):
            patcher.stop()
        return False


if __name__ == "__main__":
    unittest.main()
