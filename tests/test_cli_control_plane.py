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
    Capability,
    CommandPolicy,
    Decision,
    ErrorCode,
    ProjectRuntime,
    StaticPermissionPolicy,
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
        validation = Mock()
        validation.validate.return_value = ToolResult.ok(
            {"path": "task.json", "task_id": "TASK-001", "title": "Control plane validation"}
        )
        runtime = Mock(validation=validation)
        stdout = io.StringIO()

        with (
            patch("apos.cli.ProjectRuntime.create", return_value=runtime) as create_runtime,
            patch("apos.cli.TaskSpec.load", side_effect=AssertionError("legacy loader called")),
            patch("apos.cli.Kernel", side_effect=AssertionError("legacy Kernel called")),
            patch("apos.cli.GitClient", side_effect=AssertionError("legacy GitClient called")),
            patch("apos.cli.subprocess.run", side_effect=AssertionError("direct subprocess called")),
            redirect_stdout(stdout),
        ):
            return_code = main(["validate", "task.json"])

        self.assertEqual(return_code, 0)
        create_runtime.assert_called_once()
        validation.validate.assert_called_once()
        path, = validation.validate.call_args.args
        actor = validation.validate.call_args.kwargs["actor"]
        self.assertEqual(path, "task.json")
        self.assertEqual(actor, Actor(ActorKind.USER, "local-cli"))
        self.assertIn("TaskSpec 검증 완료", stdout.getvalue())

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

    @staticmethod
    def _runtime(root: Path) -> ProjectRuntime:
        return ProjectRuntime.create(
            root,
            permission_policy=StaticPermissionPolicy(
                {Capability.PROJECT_READ: Decision.ALLOW},
                policy_id="test-validate-read-only-v1",
            ),
            command_policy=CommandPolicy.current_python(),
        )

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
