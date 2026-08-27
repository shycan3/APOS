import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from apos.core import (
    Actor,
    ActorKind,
    ApprovalGrant,
    AuditLog,
    AuthorizationService,
    Capability,
    CommandPolicy,
    CommandRequest,
    ControlledExecutionService,
    Decision,
    ErrorCode,
    NetworkPolicy,
    PermissionEngine,
    ProjectWorkspace,
    ResourceLimits,
    StaticPermissionPolicy,
)


class ControlledExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        self.workspace = ProjectWorkspace.register(self.root)
        self.actor = Actor(ActorKind.EXTERNAL_AI, "execution-test-agent")
        self.audit = AuditLog(self.workspace)
        policy = StaticPermissionPolicy(
            {
                Capability.PROCESS_EXECUTE: Decision.ALLOW,
                Capability.TEST_EXECUTE: Decision.ALLOW,
                Capability.NETWORK_ACCESS: Decision.APPROVAL_REQUIRED,
            }
        )
        authorization = AuthorizationService(PermissionEngine(policy), self.audit)
        self.service = ControlledExecutionService(
            self.workspace,
            authorization,
            CommandPolicy.current_python(),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _request(
        self,
        *args: str,
        request_id: str | None = None,
        cwd: str = "",
        timeout: float = 10,
        output_limit: int = 4096,
        environment: dict[str, str] | None = None,
        executable: str | None = None,
        capability: Capability = Capability.PROCESS_EXECUTE,
        network_policy: NetworkPolicy = NetworkPolicy.DENIED,
    ) -> CommandRequest:
        values = {
            "executable": executable or sys.executable,
            "actor": self.actor,
            "args": tuple(args),
            "cwd": cwd,
            "environment": environment or {},
            "network_policy": network_policy,
            "limits": ResourceLimits(timeout_seconds=timeout, max_output_bytes_per_stream=output_limit),
            "capability": capability,
        }
        if request_id is not None:
            values["request_id"] = request_id
        return CommandRequest(**values)

    def test_runs_without_shell_inside_project_and_audits_lifecycle(self):
        request = self._request(
            "-c",
            "from pathlib import Path; Path('ran.txt').write_text('ok'); print('done')",
            request_id="run-1",
            capability=Capability.TEST_EXECUTE,
        )

        result = self.service.run(request)

        self.assertTrue(result.success, result.to_dict())
        self.assertEqual((self.root / "ran.txt").read_text(encoding="utf-8"), "ok")
        self.assertEqual(result.data["stdout"].strip(), "done")
        self.assertFalse(result.data["shell"])
        self.assertEqual(
            [event["status"] for event in self.audit.events(request_id="run-1")],
            ["REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED"],
        )

    def test_shell_metacharacters_in_argument_are_data(self):
        marker = self.root / "injected.txt"
        payload = f"; echo injected > {marker}"
        request = self._request("-c", "import sys; print(sys.argv[1])", payload)

        result = self.service.run(request)

        self.assertTrue(result.success, result.to_dict())
        self.assertIn("; echo injected", result.data["stdout"])
        self.assertFalse(marker.exists())

    def test_rejects_shell_syntax_and_project_path_hijacking(self):
        fake_name = "python.exe" if os.name == "nt" else "python"
        (self.root / fake_name).write_text("not executable", encoding="utf-8")

        shell = self.service.run(self._request(executable="python;whoami"))
        relative = self.service.run(self._request(executable=f".{os.sep}{fake_name}"))

        self.assertEqual(shell.error.code, ErrorCode.COMMAND_NOT_ALLOWED)
        self.assertEqual(relative.error.code, ErrorCode.COMMAND_NOT_ALLOWED)

    def test_rejects_outside_and_junction_working_directories(self):
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp)
            absolute = self.service.run(self._request("-c", "print('no')", cwd=str(outside)))
            link = self.root / "outside-link"
            self._create_directory_link(link, outside)
            linked = self.service.run(self._request("-c", "print('no')", cwd="outside-link"))

        self.assertEqual(absolute.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)
        self.assertEqual(linked.error.code, ErrorCode.PATH_OUTSIDE_PROJECT)

    def test_timeout_kills_child_process_tree(self):
        marker = self.root / "child-survived.txt"
        child_code = (
            "import time; from pathlib import Path; time.sleep(1.5); "
            "Path('child-survived.txt').write_text('bad')"
        )
        parent_code = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(30)"
        )

        result = self.service.run(self._request("-c", parent_code, timeout=0.4, request_id="timeout-1"))
        time.sleep(2)

        self.assertEqual(result.error.code, ErrorCode.EXECUTION_TIMEOUT)
        self.assertFalse(marker.exists())
        self.assertEqual(self.audit.events(request_id="timeout-1")[-1]["status"], "FAILED")

    def test_external_cancellation_kills_running_process(self):
        request = self._request("-c", "import time; time.sleep(30)", request_id="cancel-1", timeout=40)
        result_holder = []
        worker = threading.Thread(target=lambda: result_holder.append(self.service.run(request)))
        worker.start()
        deadline = time.monotonic() + 5
        while "cancel-1" not in self.service.active_request_ids() and time.monotonic() < deadline:
            time.sleep(0.02)

        cancelled = self.service.cancel("cancel-1")
        worker.join(timeout=10)

        self.assertTrue(cancelled)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result_holder[0].error.code, ErrorCode.EXECUTION_CANCELLED)
        self.assertEqual(self.audit.events(request_id="cancel-1")[-1]["status"], "CANCELLED")

    def test_bounds_large_output(self):
        result = self.service.run(
            self._request("-c", "print('x' * 10000)", output_limit=128)
        )

        self.assertTrue(result.success, result.to_dict())
        self.assertLessEqual(len(result.data["stdout"].encode("utf-8")), 128)
        self.assertTrue(result.data["stdout_truncated"])

    def test_sanitizes_environment_and_redacts_secret_output_and_audit(self):
        previous = os.environ.get("APOS_TEST_TOKEN")
        os.environ["APOS_TEST_TOKEN"] = "host-top-secret"
        try:
            code = (
                "import os; print(os.environ.get('APOS_TEST_TOKEN', '<missing>')); "
                "print('host-top-secret')"
            )
            result = self.service.run(self._request("-c", code, request_id="secret-output-1"))
        finally:
            if previous is None:
                os.environ.pop("APOS_TEST_TOKEN", None)
            else:
                os.environ["APOS_TEST_TOKEN"] = previous

        self.assertTrue(result.success, result.to_dict())
        self.assertIn("<missing>", result.data["stdout"])
        self.assertNotIn("host-top-secret", result.data["stdout"])
        self.assertIn("[REDACTED]", result.data["stdout"])
        self.assertNotIn("host-top-secret", self.audit.path.read_text(encoding="utf-8"))

    def test_rejects_environment_injection_and_explicit_network_command(self):
        environment = self.service.run(
            self._request("-c", "print('no')", environment={"PYTHONPATH": "outside"})
        )
        network = self.service.run(
            self._request("-m", "pip", "install", "example", network_policy=NetworkPolicy.DENIED)
        )

        self.assertEqual(environment.error.code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(network.error.code, ErrorCode.NETWORK_ACCESS_DENIED)

    def test_network_access_has_separate_approval_request(self):
        request = self._request(
            "-c", "print('network policy only')", request_id="network-parent",
            network_policy=NetworkPolicy.APPROVAL_REQUIRED,
        )
        pending = self.service.run(request)
        network_grant = ApprovalGrant(
            request_id="network-parent:network",
            project_id=self.workspace.project_id,
            request_digest=pending.meta["permission_request_digest"],
            approved_by=Actor(ActorKind.USER, "owner"),
            note="Approved network capability for this request.",
        )

        approved = self.service.run(request, network_approval=network_grant)

        self.assertEqual(pending.error.code, ErrorCode.PERMISSION_REQUIRED)
        self.assertTrue(approved.success, approved.to_dict())
        self.assertEqual(approved.data["network_enforcement"], "DECLARATIVE_ONLY")

    def test_permission_approval_is_bound_to_exact_request(self):
        audit = AuditLog(self.workspace)
        authorization = AuthorizationService(
            PermissionEngine(
                StaticPermissionPolicy({Capability.PROCESS_EXECUTE: Decision.APPROVAL_REQUIRED})
            ),
            audit,
        )
        service = ControlledExecutionService(
            self.workspace, authorization, CommandPolicy.current_python()
        )
        request = self._request("-c", "print('approved')", request_id="approval-1")
        pending = service.run(request)
        grant = ApprovalGrant(
            request_id="approval-1",
            project_id=self.workspace.project_id,
            request_digest=pending.meta["permission_request_digest"],
            approved_by=Actor(ActorKind.USER, "owner"),
            note="Approved exact command request.",
        )

        approved = service.run(request, approval=grant)

        self.assertEqual(pending.error.code, ErrorCode.PERMISSION_REQUIRED)
        self.assertTrue(approved.success, approved.to_dict())

    def test_permission_approval_cannot_be_reused_with_changed_environment(self):
        audit = AuditLog(self.workspace)
        authorization = AuthorizationService(
            PermissionEngine(
                StaticPermissionPolicy({Capability.PROCESS_EXECUTE: Decision.APPROVAL_REQUIRED})
            ),
            audit,
        )
        service = ControlledExecutionService(
            self.workspace, authorization, CommandPolicy.current_python()
        )
        original = self._request(
            "-c", "print('ok')", request_id="env-approval", environment={"MODE": "one"}
        )
        pending = service.run(original)
        grant = ApprovalGrant(
            request_id="env-approval",
            project_id=self.workspace.project_id,
            request_digest=pending.meta["permission_request_digest"],
            approved_by=Actor(ActorKind.USER, "owner"),
            note="Approved only MODE=one.",
        )
        changed = self._request(
            "-c", "print('ok')", request_id="env-approval", environment={"MODE": "two"}
        )

        rejected = service.run(changed, approval=grant)

        self.assertEqual(rejected.error.code, ErrorCode.PERMISSION_DENIED)

    def _create_directory_link(self, link: Path, target: Path) -> None:
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            if os.name != "nt":
                self.skipTest(f"symlink creation is unavailable: {exc}")
            junction = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                text=True,
                capture_output=True,
            )
            if junction.returncode != 0:
                self.skipTest(f"symlink and junction creation are unavailable: {exc}")


if __name__ == "__main__":
    unittest.main()
