from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from apos.core import (
    Actor,
    ActorKind,
    ApprovalAction,
    ApprovalGrant,
    ApprovalSource,
    AuditLog,
    Capability,
    CommandPolicy,
    CommandRequest,
    Decision,
    ErrorCode,
    PermissionEngine,
    PermissionRequest,
    ProjectRuntime,
    ProjectWorkspace,
    ResourceLimits,
    RiskLevel,
    SQLiteTaskRepository,
    StaticPermissionPolicy,
    TaskError,
    TaskService,
    TaskState,
    TaskStateMachine,
)


class PersistentTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = ProjectWorkspace.register(self.root)
        self.audit = AuditLog(self.workspace)
        self.repository = SQLiteTaskRepository(self.workspace)
        self.tasks = TaskService(self.workspace, self.repository, self.audit)
        self.actor = Actor(ActorKind.EXTERNAL_AI, "task-agent")
        self.user = Actor(ActorKind.USER, "local-owner")

    def tearDown(self):
        self.temporary.cleanup()

    def _request(
        self,
        task_id: str = "task-1",
        request_id: str = "request-1",
        *,
        metadata: dict | None = None,
    ) -> PermissionRequest:
        return PermissionRequest.create(
            project_id=self.workspace.project_id,
            actor=self.actor,
            capability=Capability.PROCESS_EXECUTE,
            resource="trusted-python",
            operation="execution.run",
            risk_level=RiskLevel.HIGH,
            metadata=metadata or {"args_digest": "safe-digest"},
            request_id=request_id,
            task_id=task_id,
        )

    def _waiting(self, task_id: str = "task-1", request_id: str = "request-1"):
        request = self._request(task_id, request_id)
        self.tasks.create_task(request, description="Persistent test task")
        self.tasks.queue_task(task_id, actor=self.actor)
        self.tasks.request_approval(task_id, actor=self.actor)
        return request

    def _approve(
        self,
        request: PermissionRequest,
        *,
        expires_at: str | None = None,
        subject: Actor | None = None,
        approved_by: Actor | None = None,
        source: ApprovalSource = ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
        authenticated: bool = False,
    ):
        return self.tasks.grant_approval(
            request.task_id,
            action=ApprovalAction(
                request_id=request.request_id,
                request_digest=request.digest(),
                subject=subject or request.actor,
                approved_by=approved_by or self.user,
                source=source,
                note="Explicit local approval for one request.",
                expires_at=expires_at,
                authenticated=authenticated,
            ),
        )

    def test_task_creation_is_persistent_and_duplicate_ids_are_rejected(self):
        request = self._request()
        created = self.tasks.create_task(request, description="Create task")

        reopened = TaskService(
            self.workspace, SQLiteTaskRepository(self.workspace), self.audit
        )

        self.assertEqual(created.state, TaskState.CREATED)
        self.assertEqual(reopened.get_task("task-1").permission_request_id, "request-1")
        with self.assertRaises(TaskError) as duplicate:
            reopened.create_task(request, description="Duplicate")
        self.assertEqual(duplicate.exception.code, ErrorCode.TASK_ALREADY_EXISTS)

    def test_state_machine_allows_defined_transitions_and_rejects_terminal_restart(self):
        request = self._request()
        self.tasks.create_task(request, description="State transitions")
        self.assertEqual(self.tasks.queue_task("task-1", actor=self.actor).state, TaskState.QUEUED)
        self.assertEqual(
            self.tasks.request_approval("task-1", actor=self.actor).state,
            TaskState.WAITING_APPROVAL,
        )
        cancelled = self.tasks.cancel_task("task-1", actor=self.actor, reason="operator cancelled")

        self.assertEqual(cancelled.state, TaskState.CANCELLED)
        with self.assertRaises(TaskError) as invalid:
            self.tasks.queue_task("task-1", actor=self.actor)
        self.assertEqual(invalid.exception.code, ErrorCode.INVALID_TASK_TRANSITION)
        with self.assertRaises(TaskError):
            TaskStateMachine.validate(TaskState.SUCCEEDED, TaskState.RUNNING)

    def test_waiting_approval_survives_restart(self):
        self._waiting()

        restarted = TaskService(
            self.workspace, SQLiteTaskRepository(self.workspace), self.audit
        )

        self.assertEqual(restarted.get_task("task-1").state, TaskState.WAITING_APPROVAL)

    def test_approval_persists_and_is_distinct_from_execution_started(self):
        request = self._waiting()
        approval = self._approve(request)

        restarted = TaskService(
            self.workspace, SQLiteTaskRepository(self.workspace), self.audit
        )

        self.assertEqual(restarted.get_task("task-1").state, TaskState.APPROVED)
        self.assertIsNone(restarted.get_approval("task-1").consumed_at)
        self.assertEqual(restarted.get_approval("task-1").approval_id, approval.approval_id)

    def test_approval_rejects_changed_request_and_changed_task_id(self):
        request = self._waiting()
        approval = self._approve(request).to_grant()
        changed_resource = PermissionRequest.create(
            project_id=request.project_id,
            actor=request.actor,
            capability=request.capability,
            resource="different-resource",
            operation=request.operation,
            risk_level=request.risk_level,
            metadata=request.metadata,
            request_id=request.request_id,
            task_id=request.task_id,
        )
        changed_task = PermissionRequest.create(
            project_id=request.project_id,
            actor=request.actor,
            capability=request.capability,
            resource=request.resource,
            operation=request.operation,
            risk_level=request.risk_level,
            metadata=request.metadata,
            request_id=request.request_id,
            task_id="different-task",
        )

        resource_result = self.tasks.consume_approval(changed_resource, approval)
        task_result = self.tasks.consume_approval(changed_task, approval)

        self.assertFalse(resource_result.allowed)
        self.assertFalse(task_result.allowed)
        self.assertEqual(self.tasks.get_task("task-1").state, TaskState.APPROVED)

    def test_consumed_approval_cannot_be_reused_after_restart(self):
        request = self._waiting()
        grant = self._approve(request).to_grant()

        first = self.tasks.consume_approval(request, grant)
        restarted = TaskService(
            self.workspace, SQLiteTaskRepository(self.workspace), self.audit
        )
        replay = restarted.consume_approval(request, grant)

        self.assertTrue(first.allowed)
        self.assertFalse(replay.allowed)
        self.assertEqual(replay.error_code, ErrorCode.APPROVAL_ALREADY_CONSUMED)
        self.assertEqual(restarted.get_task("task-1").state, TaskState.RECOVERY_REQUIRED)

    def test_expired_approval_cannot_be_consumed(self):
        request = self._waiting()
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        grant = self._approve(request, expires_at=expired_at).to_grant()

        result = self.tasks.consume_approval(request, grant)

        self.assertFalse(result.allowed)
        self.assertEqual(result.error_code, ErrorCode.APPROVAL_EXPIRED)
        self.assertEqual(self.tasks.get_task("task-1").state, TaskState.EXPIRED)

    def test_nonexistent_approval_is_rejected(self):
        request = self._waiting()
        missing = ApprovalGrant(
            request_id=request.request_id,
            project_id=request.project_id,
            request_digest=request.digest(),
            approved_by=self.user,
            note="Missing approval",
            grant_id="missing-grant",
        )

        result = self.tasks.consume_approval(request, missing)

        self.assertFalse(result.allowed)
        self.assertEqual(result.error_code, ErrorCode.APPROVAL_NOT_FOUND)

    def test_approval_subject_must_match_task_actor(self):
        request = self._waiting()

        with self.assertRaises(TaskError) as mismatch:
            self._approve(request, subject=Actor(ActorKind.EXTERNAL_AI, "different-agent"))

        self.assertEqual(mismatch.exception.code, ErrorCode.APPROVAL_SUBJECT_MISMATCH)
        self.assertEqual(self.tasks.get_task("task-1").state, TaskState.WAITING_APPROVAL)

    def test_task_cannot_enter_running_without_approval_consumption(self):
        self._waiting()

        with self.assertRaises(TaskError) as invalid:
            self.repository.transition(
                "task-1",
                TaskState.RUNNING,
                actor=self.actor,
                event_types=("TASK_STARTED",),
            )

        self.assertEqual(invalid.exception.code, ErrorCode.INVALID_TASK_TRANSITION)

    def test_persistent_task_requires_approval_even_when_capability_policy_allows(self):
        request = self._waiting()
        engine = PermissionEngine(
            StaticPermissionPolicy({Capability.PROCESS_EXECUTE: Decision.ALLOW}),
            approval_consumer=self.tasks,
        )

        pending = engine.evaluate(request)
        grant = self._approve(request).to_grant()
        approved = engine.evaluate(request, approval=grant)

        self.assertEqual(pending.decision, Decision.APPROVAL_REQUIRED)
        self.assertEqual(approved.decision, Decision.ALLOW)
        self.assertEqual(self.tasks.get_task("task-1").state, TaskState.RUNNING)

    def test_ai_and_system_cannot_issue_human_approval(self):
        request = self._waiting()
        for kind, source in (
            (ActorKind.EXTERNAL_AI, ApprovalSource.AI),
            (ActorKind.SYSTEM, ApprovalSource.SYSTEM),
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(TaskError) as denied:
                    self._approve(
                        request,
                        approved_by=Actor(kind, f"{kind.value.lower()}-issuer"),
                        source=source,
                    )
                self.assertEqual(denied.exception.code, ErrorCode.PERMISSION_DENIED)

    def test_authenticated_human_source_is_explicitly_unimplemented(self):
        request = self._waiting()

        with self.assertRaises(TaskError) as unavailable:
            self._approve(
                request,
                source=ApprovalSource.AUTHENTICATED_HUMAN,
                authenticated=True,
            )

        self.assertEqual(
            unavailable.exception.code, ErrorCode.HUMAN_AUTHENTICATION_UNIMPLEMENTED
        )

    def test_two_workers_cannot_consume_one_approval_or_start_one_task_twice(self):
        request = self._waiting()
        grant = self._approve(request).to_grant()
        workers = [
            TaskService(self.workspace, SQLiteTaskRepository(self.workspace), self.audit)
            for _ in range(2)
        ]
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def consume(worker):
            try:
                barrier.wait(timeout=5)
                results.append(worker.consume_approval(request, grant))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=consume, args=(worker,)) for worker in workers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sum(result.allowed for result in results), 1)
        self.assertEqual(self.tasks.get_task("task-1").state, TaskState.RUNNING)

    def test_task_and_approval_lifecycle_are_linked_to_audit(self):
        request = self._waiting()
        grant = self._approve(request).to_grant()
        self.tasks.consume_approval(request, grant)
        self.tasks.complete_task("task-1", actor=self.actor, succeeded=True)

        events = self.audit.events(request_id=request.request_id)
        event_types = [event["metadata"]["event_type"] for event in events]

        self.assertEqual(
            event_types,
            [
                "TASK_CREATED",
                "TASK_QUEUED",
                "TASK_WAITING_APPROVAL",
                "APPROVAL_REQUESTED",
                "APPROVAL_GRANTED",
                "TASK_APPROVED",
                "APPROVAL_CONSUMED",
                "TASK_STARTED",
                "TASK_SUCCEEDED",
            ],
        )
        self.assertTrue(all(event["task_id"] == "task-1" for event in events))

    def test_task_storage_and_audit_redact_secret_text(self):
        request = self._request()
        self.tasks.create_task(
            request,
            description="API_TOKEN=plain-secret",
            metadata={"api_key": "plain-secret", "mode": "test"},
        )

        raw_database = self.repository.path.read_bytes()
        raw_audit = self.audit.path.read_text(encoding="utf-8")
        task = self.tasks.get_task("task-1")

        self.assertNotIn(b"plain-secret", raw_database)
        self.assertNotIn("plain-secret", raw_audit)
        self.assertIn("[REDACTED]", task.description)
        self.assertEqual(task.metadata["api_key"], "[REDACTED]")

    def test_permission_request_with_secret_material_is_not_persisted(self):
        request = self._request(metadata={"api_key": "plain-secret"})

        with self.assertRaises(TaskError) as denied:
            self.tasks.create_task(request, description="Unsafe request")

        self.assertEqual(denied.exception.code, ErrorCode.PERMISSION_DENIED)

    def test_corrupted_persistence_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = ProjectWorkspace.register(root)
            path = root / ".apos" / "state" / "tasks.sqlite3"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not-a-sqlite-database")

            with self.assertRaises(TaskError) as corrupted:
                SQLiteTaskRepository(workspace, path=path).initialize()

            self.assertEqual(corrupted.exception.code, ErrorCode.PERSISTENCE_CORRUPTED)

    def test_running_task_recovers_without_automatic_execution(self):
        request = self._waiting()
        grant = self._approve(request).to_grant()
        self.assertTrue(self.tasks.consume_approval(request, grant).allowed)

        restarted = TaskService(
            self.workspace, SQLiteTaskRepository(self.workspace), self.audit
        )
        recovered = restarted.get_task("task-1")

        self.assertEqual(recovered.state, TaskState.RECOVERY_REQUIRED)
        self.assertIsNotNone(restarted.get_approval("task-1").consumed_at)
        recovery_events = [
            event
            for event in self.audit.events(request_id=request.request_id)
            if event["metadata"].get("event_type") == "TASK_RECOVERY_REQUIRED"
        ]
        self.assertEqual(len(recovery_events), 1)
        self.assertFalse(recovery_events[0]["metadata"]["automatic_execution_resumed"])

    def test_task_id_alone_cannot_mutate_task_owned_by_another_actor(self):
        request = self._request()
        self.tasks.create_task(request, description="Actor binding")

        with self.assertRaises(TaskError) as mismatch:
            self.tasks.queue_task(
                "task-1", actor=Actor(ActorKind.EXTERNAL_AI, "other-agent")
            )

        self.assertEqual(mismatch.exception.code, ErrorCode.APPROVAL_SUBJECT_MISMATCH)


class RuntimeTaskIntegrationTests(unittest.TestCase):
    def test_runtime_execution_consumes_persistent_approval_before_process_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {Capability.PROCESS_EXECUTE: Decision.APPROVAL_REQUIRED}
                ),
                command_policy=CommandPolicy.current_python(),
            )
            actor = Actor(ActorKind.EXTERNAL_AI, "runtime-agent")
            args = ("-c", "print('persistent approval')")
            limits = ResourceLimits(timeout_seconds=10, max_output_bytes_per_stream=4096)
            command = CommandRequest(
                executable=sys.executable,
                actor=actor,
                args=args,
                limits=limits,
                request_id="runtime-request",
                task_id="runtime-task",
            )
            executable = str(Path(sys.executable).resolve())
            permission_request = PermissionRequest.create(
                project_id=runtime.workspace.project_id,
                actor=actor,
                capability=Capability.PROCESS_EXECUTE,
                resource=executable,
                operation="execution.run",
                risk_level=RiskLevel.HIGH,
                metadata={
                    "executable": executable,
                    "argument_count": len(args),
                    "args_digest": _digest(list(args)),
                    "cwd": ".",
                    "environment_keys": [],
                    "environment_digest": _digest({}),
                    "network_policy": "NETWORK_DENIED",
                    "timeout_seconds": limits.timeout_seconds,
                    "output_limit_bytes_per_stream": limits.max_output_bytes_per_stream,
                    "shell": False,
                },
                request_id=command.request_id,
                task_id=command.task_id,
            )
            runtime.tasks.create_task(permission_request, description="Run approved Python")
            runtime.tasks.queue_task(command.task_id, actor=actor)
            runtime.tasks.request_approval(command.task_id, actor=actor)
            approval = runtime.tasks.grant_approval(
                command.task_id,
                action=ApprovalAction(
                    request_id=permission_request.request_id,
                    request_digest=permission_request.digest(),
                    subject=actor,
                    approved_by=Actor(ActorKind.USER, "local-owner"),
                    source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                    note="Local integration test approval.",
                ),
            )

            result = runtime.tasks.run_command_task(command, runtime.execution)

            self.assertTrue(result.success, result.to_dict())
            self.assertEqual(runtime.tasks.get_task(command.task_id).state, TaskState.SUCCEEDED)


def _digest(value) -> str:
    serialized = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
