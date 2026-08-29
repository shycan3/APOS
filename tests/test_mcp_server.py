from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from apos.application import APOSApplicationService, APOSStatus
from apos.core.result import ErrorCode, ToolResult
from apos.kernel import RunOptions
from apos.mcp_server import APOSMCPTools, MAX_RUN_LIMIT, build_server
from apos.models import AttemptResult, RunSummary, TaskSpec
from apos.runlog import RunLogEntry


def valid_task() -> dict[str, object]:
    return {
        "task_id": "mcp-demo",
        "goal": "Exercise the MCP adapter",
        "allowed_files": ["README.md"],
        "test_commands": ["python -m unittest"],
    }


def summary(status: str = "SUCCEEDED") -> RunSummary:
    return RunSummary(
        status=status,
        task_id="mcp-demo",
        branch="apos/task/mcp-demo",
        attempts=[AttemptResult(attempt=1, status=status, message="done")],
        committed=False,
        commit_hash=None,
        run_log=".apos/runs/mcp-demo/run-1",
    )


@dataclass(frozen=True)
class FakeRunEntry:
    path: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "status": "SUCCEEDED"}


class MCPServerTests(unittest.TestCase):
    def test_status_delegates_to_application_service(self) -> None:
        service = Mock()
        service.get_status.return_value = APOSStatus(
            root="/repo",
            version="1.2.0",
            branch="master",
            dirty=False,
            status_porcelain=[],
            coder_command_configured=False,
        )

        result = APOSMCPTools(service).apos_status()

        self.assertTrue(result["success"])
        self.assertEqual(result["status"]["root"], "/repo")
        service.get_status.assert_called_once_with()

    def test_validate_task_accepts_exactly_one_source(self) -> None:
        tools = APOSMCPTools(Mock())

        neither = tools.apos_validate_task()
        both = tools.apos_validate_task(task_path="task.json", task=valid_task())

        self.assertFalse(neither["success"])
        self.assertEqual(neither["error"]["code"], "INVALID_TASK_SOURCE")
        self.assertFalse(both["success"])
        self.assertEqual(both["error"]["code"], "INVALID_TASK_SOURCE")

    def test_validate_inline_task_uses_task_spec_mapping(self) -> None:
        tools = APOSMCPTools(Mock())

        accepted = tools.apos_validate_task(task=valid_task())
        rejected = tools.apos_validate_task(task={"task_id": "missing-rules", "goal": "invalid"})

        self.assertTrue(accepted["success"])
        self.assertEqual(accepted["validation"]["taskspec"]["task_id"], "mcp-demo")
        self.assertFalse(rejected["success"])
        self.assertEqual(rejected["error"]["code"], "INVALID_TASK")
        self.assertIn("allowed_files", rejected["error"]["message"])

    def test_validate_path_delegates_to_application_service(self) -> None:
        service = Mock()
        service.validate_task.return_value = ToolResult.ok({"taskspec": valid_task(), "source": "path"})

        result = APOSMCPTools(service).apos_validate_task(task_path="tasks/demo.json")

        self.assertTrue(result["success"])
        self.assertEqual(result["validation"]["taskspec"]["task_id"], "mcp-demo")
        service.validate_task.assert_called_once_with("tasks/demo.json")

    def test_validate_path_preserves_application_service_errors(self) -> None:
        service = Mock()
        service.validate_task.return_value = ToolResult.fail(
            ErrorCode.PATH_OUTSIDE_PROJECT,
            "path is outside project",
            details={"path": "../task.json"},
        )

        result = APOSMCPTools(service).apos_validate_task(task_path="../task.json")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "PATH_OUTSIDE_PROJECT")
        service.validate_task.assert_called_once_with("../task.json")

    def test_run_path_delegates_to_application_service_with_exposed_options(self) -> None:
        service = Mock()
        service.run_task_file.return_value = summary()

        result = APOSMCPTools(service).apos_run_task(
            task_path="tasks/demo.json",
            no_commit=True,
            command_timeout_seconds=7,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["status"], "SUCCEEDED")
        service.run_task_file.assert_called_once()
        self.assertEqual(service.run_task_file.call_args.args[0], "tasks/demo.json")
        options = service.run_task_file.call_args.args[1]
        self.assertIsInstance(options, RunOptions)
        self.assertTrue(options.no_commit)
        self.assertEqual(options.command_timeout_seconds, 7)

    def test_run_inline_delegates_canonical_task_spec_to_application_service(self) -> None:
        service = Mock()
        service.run_task.return_value = summary()

        result = APOSMCPTools(service).apos_run_task(task=valid_task())

        self.assertTrue(result["success"])
        service.run_task.assert_called_once()
        self.assertIsInstance(service.run_task.call_args.args[0], TaskSpec)
        self.assertEqual(service.run_task.call_args.args[0].task_id, "mcp-demo")

    def test_recovery_required_remains_successful_domain_result(self) -> None:
        service = Mock()
        service.run_task_file.return_value = summary("RECOVERY_REQUIRED")

        result = APOSMCPTools(service).apos_run_task(task_path="tasks/demo.json")

        self.assertTrue(result["success"])
        self.assertEqual(result["summary"]["status"], "RECOVERY_REQUIRED")

    def test_invalid_run_options_are_rejected_before_execution(self) -> None:
        service = Mock()
        tools = APOSMCPTools(service)

        no_commit_result = tools.apos_run_task(task_path="task.json", no_commit="yes")  # type: ignore[arg-type]
        timeout_result = tools.apos_run_task(task_path="task.json", command_timeout_seconds=0)

        self.assertFalse(no_commit_result["success"])
        self.assertFalse(timeout_result["success"])
        service.run_task_file.assert_not_called()

    def test_get_run_and_list_runs_delegate_to_application_service(self) -> None:
        service = Mock()
        service.get_run.return_value = {"summary": {"status": "SUCCEEDED"}}
        service.list_runs.return_value = [FakeRunEntry(".apos/runs/mcp-demo/run-1")]
        tools = APOSMCPTools(service)

        got = tools.apos_get_run(".apos/runs/mcp-demo/run-1")
        listed = tools.apos_list_runs(limit=1)

        self.assertTrue(got["success"])
        self.assertEqual(got["run"]["summary"]["status"], "SUCCEEDED")
        self.assertTrue(listed["success"])
        self.assertEqual(listed["runs"], [{"path": ".apos/runs/mcp-demo/run-1", "status": "SUCCEEDED"}])
        service.get_run.assert_called_once_with(".apos/runs/mcp-demo/run-1")
        service.list_runs.assert_called_once_with(limit=1)

    def test_list_runs_rejects_pathological_limits(self) -> None:
        service = Mock()
        tools = APOSMCPTools(service)

        for limit in (0, MAX_RUN_LIMIT + 1, True):
            with self.subTest(limit=limit):
                result = tools.apos_list_runs(limit=limit)  # type: ignore[arg-type]
                self.assertFalse(result["success"])
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        service.list_runs.assert_not_called()

    def test_get_run_rejects_empty_path_before_service_call(self) -> None:
        service = Mock()

        result = APOSMCPTools(service).apos_get_run("")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        service.get_run.assert_not_called()

    def test_build_server_fixes_root_once_and_uses_application_service(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("apos.mcp_server.APOSApplicationService") as service_class:
                server = build_server(root=root)

        self.assertEqual(server.name, "apos")
        service_class.assert_called_once_with(root.resolve())

    def test_mcp_module_does_not_import_kernel_or_legacy_git_clients(self) -> None:
        import apos.mcp_server as mcp_server

        self.assertFalse(hasattr(mcp_server, "Kernel"))
        self.assertFalse(hasattr(mcp_server, "GitClient"))
        self.assertFalse(hasattr(mcp_server, "ControlledGitClient"))


class MCPApplicationBoundaryTests(unittest.TestCase):
    def test_real_application_service_rejects_escaped_task_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            service = APOSApplicationService(root)

            result = APOSMCPTools(service).apos_validate_task(task_path="../outside.json")

        self.assertFalse(result["success"])
        self.assertEqual(result["error"]["code"], "PATH_OUTSIDE_PROJECT")


class ImportedTypeSmokeTests(unittest.TestCase):
    def test_run_log_entry_shape_remains_serializable_for_list_runs(self) -> None:
        entry = RunLogEntry(
            path=Path(".apos/runs/task/run"),
            relative_path=".apos/runs/task/run",
            task_id="task",
            title="Task",
            status="SUCCEEDED",
            branch="master",
            started_at="20260101T000000Z",
            attempts=1,
            committed=False,
            commit_hash=None,
        )

        self.assertEqual(entry.to_dict()["path"], ".apos/runs/task/run")


if __name__ == "__main__":
    unittest.main()
