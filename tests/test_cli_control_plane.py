import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from apos.cli import main
from apos.core import (
    Actor,
    ActorKind,
    ApprovalAction,
    ApprovalSource,
    Capability,
    ErrorCode,
    PermissionRequest,
    ProjectRuntime,
    RiskLevel,
    TaskState,
    ToolResult,
)


class ValidateControlPlaneTests(unittest.TestCase):
    def test_production_cli_reads_through_runtime_and_records_audit_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            taskspec = root / "task.json"
            taskspec.write_text(json.dumps(self._taskspec()), encoding="utf-8")
            before = taskspec.read_bytes()

            stdout = io.StringIO()
            with patch("apos.cli.Path.cwd", return_value=root), redirect_stdout(stdout):
                return_code = main(["validate", "task.json"])

            events_path = root / ".apos" / "audit" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(return_code, 0)
            self.assertIn("TaskSpec 검증 완료: TASK-001 (Control plane validation)", stdout.getvalue())
            self.assertEqual(taskspec.read_bytes(), before)
            self.assertEqual(
                [event["status"] for event in events],
                ["REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED"],
            )
            self.assertTrue(all(event["operation"] == "filesystem.read" for event in events))
            self.assertTrue(all(event["capability"] == "PROJECT_READ" for event in events))
            self.assertTrue(all(event["resource"] == "task.json" for event in events))
            self.assertTrue(all(event["actor"] == {"kind": "USER", "actor_id": "local-cli"} for event in events))
            self.assertEqual(len({event["request_id"] for event in events}), 1)

    def test_cli_adapter_does_not_reenter_legacy_validate_dependencies(self):
        service = Mock()
        service.validate_task.return_value = ToolResult.ok(
            {"path": "task.json", "task_id": "TASK-001", "title": "Control plane validation"}
        )
        stdout = io.StringIO()

        with (
            patch("apos.cli.APOSApplicationService", return_value=service) as service_factory,
            patch("apos.cli.TaskSpec.load", side_effect=AssertionError("legacy loader called")),
            patch("apos.cli.GitClient", side_effect=AssertionError("legacy GitClient called")),
            patch("apos.cli.subprocess.run", side_effect=AssertionError("direct subprocess called")),
            redirect_stdout(stdout),
        ):
            return_code = main(["validate", "task.json"])

        self.assertEqual(return_code, 0)
        service_factory.assert_called_once()
        service.validate_task.assert_called_once()
        path, = service.validate_task.call_args.args
        self.assertEqual(path, Path("task.json"))
        self.assertIn("TaskSpec 검증 완료", stdout.getvalue())

    def test_validate_does_not_recover_an_unrelated_running_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.json").write_text(json.dumps(self._taskspec()), encoding="utf-8")
            runtime = self._runtime(root)
            task_actor = Actor(ActorKind.EXTERNAL_AI, "running-agent")
            user = Actor(ActorKind.USER, "local-owner")
            request = PermissionRequest.create(
                project_id=runtime.workspace.project_id,
                actor=task_actor,
                capability=Capability.PROCESS_EXECUTE,
                resource="trusted-python",
                operation="execution.run",
                risk_level=RiskLevel.HIGH,
                metadata={"args_digest": "running-task"},
                request_id="running-request",
                task_id="running-task",
            )
            runtime.tasks.create_task(request, description="Concurrent running task")
            runtime.tasks.queue_task("running-task", actor=task_actor)
            runtime.tasks.request_approval("running-task", actor=task_actor)
            approval = runtime.tasks.grant_approval(
                "running-task",
                action=ApprovalAction(
                    request_id=request.request_id,
                    request_digest=request.digest(),
                    subject=task_actor,
                    approved_by=user,
                    source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                    note="Explicit local approval for the running task.",
                ),
            )
            self.assertTrue(runtime.tasks.consume_approval(request, approval.to_grant()).allowed)
            self.assertEqual(runtime.tasks.get_task("running-task").state, TaskState.RUNNING)

            with patch("apos.cli.Path.cwd", return_value=root), redirect_stdout(io.StringIO()):
                return_code = main(["validate", "task.json"])

            self.assertEqual(return_code, 0)
            self.assertEqual(runtime.tasks.get_task("running-task").state, TaskState.RUNNING)

    def test_outside_project_path_fails_closed_and_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = self._runtime(root)

            result = runtime.validation.validate(
                "../outside.json",
                actor=Actor(ActorKind.USER, "local-cli"),
                request_id="outside-read",
            )
            events = runtime.audit_log.events(request_id="outside-read")

            self.assertFalse(result.success)
            self.assertEqual(result.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)
            self.assertEqual([event["status"] for event in events], ["REQUESTED", "DENIED"])

    def test_invalid_json_returns_structured_error_after_authorized_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "invalid.json").write_text("{not-json", encoding="utf-8")
            runtime = self._runtime(root)

            result = runtime.validation.validate(
                "invalid.json",
                actor=Actor(ActorKind.USER, "local-cli"),
                request_id="invalid-json",
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error.code, ErrorCode.INVALID_ARGUMENT)
            self.assertEqual(result.error.details, {"path": "invalid.json"})
            self.assertEqual(
                [event["status"] for event in runtime.audit_log.events(request_id="invalid-json")],
                ["REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED"],
            )

    def test_cli_reports_structured_validation_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "invalid.json").write_text("{not-json", encoding="utf-8")
            stderr = io.StringIO()

            with patch("apos.cli.Path.cwd", return_value=root), redirect_stderr(stderr):
                return_code = main(["validate", "invalid.json"])

            self.assertEqual(return_code, 1)
            self.assertIn("APOS 오류: INVALID_ARGUMENT:", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_reports_runtime_initialization_failure_without_internal_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.json").write_text(json.dumps(self._taskspec()), encoding="utf-8")
            state = root / ".apos" / "state"
            state.mkdir(parents=True)
            (state / "tasks.sqlite3").write_bytes(b"not-a-sqlite-database")
            stderr = io.StringIO()

            with patch("apos.cli.Path.cwd", return_value=root), redirect_stderr(stderr):
                return_code = main(["validate", "task.json"])

            error_output = stderr.getvalue()
            self.assertEqual(return_code, 1)
            self.assertIn("APOS 오류: PERSISTENCE_CORRUPTED:", error_output)
            self.assertNotIn("Traceback", error_output)
            self.assertNotIn(str(root.resolve()), error_output)

    @staticmethod
    def _runtime(root: Path) -> ProjectRuntime:
        return ProjectRuntime.create_read_only(root)

    @staticmethod
    def _taskspec() -> dict[str, object]:
        return {
            "task_id": "TASK-001",
            "title": "Control plane validation",
            "goal": "Validate the first production control-plane migration.",
            "allowed_files": ["src/apos/cli.py"],
            "test_commands": ["python -m unittest discover -s tests"],
        }


if __name__ == "__main__":
    unittest.main()
