import json
from pathlib import Path
import tempfile
import unittest

from apos.core import (
    Actor,
    ActorKind,
    ApprovalGrant,
    ApprovalSource,
    AuditLog,
    AuditStatus,
    AuthorizationService,
    Capability,
    Decision,
    ErrorCode,
    FileSystemService,
    PermissionEngine,
    PermissionRequest,
    ProjectWorkspace,
    Redactor,
    RiskLevel,
    StaticPermissionPolicy,
)


class PermissionEngineTests(unittest.TestCase):
    def setUp(self):
        self.actor = Actor(ActorKind.EXTERNAL_AI, "external-test-agent")

    def _request(self, capability: Capability, request_id: str = "request-1") -> PermissionRequest:
        return PermissionRequest.create(
            project_id="project-1",
            actor=self.actor,
            capability=capability,
            resource="src/app.py",
            operation="test.operation",
            risk_level=RiskLevel.LOW,
            request_id=request_id,
        )

    def test_allows_explicit_safe_capability_and_denies_missing_rule(self):
        engine = PermissionEngine(StaticPermissionPolicy({Capability.PROJECT_READ: Decision.ALLOW}))

        allowed = engine.evaluate(self._request(Capability.PROJECT_READ))
        denied = engine.evaluate(self._request(Capability.PROJECT_WRITE))

        self.assertEqual(allowed.decision, Decision.ALLOW)
        self.assertEqual(denied.decision, Decision.DENY)

    def test_risky_action_requires_matching_trusted_approval(self):
        engine = PermissionEngine(
            StaticPermissionPolicy({Capability.PROCESS_EXECUTE: Decision.APPROVAL_REQUIRED})
        )
        request = self._request(Capability.PROCESS_EXECUTE, request_id="exec-1")
        pending = engine.evaluate(request)
        grant = ApprovalGrant(
            request_id="exec-1",
            project_id="project-1",
            request_digest=request.digest(),
            approved_by=Actor(ActorKind.USER, "owner"),
            note="Approved this exact execution request.",
        )

        approved = engine.evaluate(request, approval=grant)
        replayed = engine.evaluate(request, approval=grant)

        self.assertEqual(pending.decision, Decision.APPROVAL_REQUIRED)
        self.assertEqual(approved.decision, Decision.ALLOW)
        self.assertEqual(replayed.decision, Decision.DENY)

    def test_approval_digest_rejects_changed_request(self):
        engine = PermissionEngine(
            StaticPermissionPolicy({Capability.PROCESS_EXECUTE: Decision.APPROVAL_REQUIRED})
        )
        original = self._request(Capability.PROCESS_EXECUTE, request_id="exec-2")
        changed = PermissionRequest.create(
            project_id="project-1",
            actor=self.actor,
            capability=Capability.PROCESS_EXECUTE,
            resource="different-command",
            operation="test.operation",
            risk_level=RiskLevel.LOW,
            request_id="exec-2",
        )
        grant = ApprovalGrant(
            request_id="exec-2",
            project_id="project-1",
            request_digest=original.digest(),
            approved_by=Actor(ActorKind.USER, "owner"),
            note="Approved only the original request.",
        )

        decision = engine.evaluate(changed, approval=grant)

        self.assertEqual(decision.decision, Decision.DENY)

    def test_external_ai_cannot_issue_approval(self):
        with self.assertRaises(ValueError):
            ApprovalGrant(
                request_id="exec-1",
                project_id="project-1",
                request_digest="0" * 64,
                approved_by=self.actor,
                note="AI intent is not authorization.",
            )

    def test_system_actor_cannot_issue_human_approval(self):
        with self.assertRaises(ValueError):
            ApprovalGrant(
                request_id="exec-1",
                project_id="project-1",
                request_digest="0" * 64,
                approved_by=Actor(ActorKind.SYSTEM, "policy-engine"),
                note="System authorization is not human approval.",
            )

    def test_authenticated_human_grant_fails_closed_without_identity_proof(self):
        for authenticated in (False, True):
            with self.subTest(authenticated=authenticated):
                with self.assertRaises(ValueError):
                    ApprovalGrant(
                        request_id="exec-1",
                        project_id="project-1",
                        request_digest="0" * 64,
                        approved_by=Actor(ActorKind.USER, "unverified-name"),
                        note="A string identity is not proof.",
                        approval_source=ApprovalSource.AUTHENTICATED_HUMAN,
                        authenticated=authenticated,
                    )

    def test_system_authorization_is_a_policy_decision_not_an_approval_grant(self):
        engine = PermissionEngine(
            StaticPermissionPolicy({Capability.PROJECT_READ: Decision.ALLOW})
        )

        decision = engine.evaluate(self._request(Capability.PROJECT_READ))

        self.assertEqual(decision.decision, Decision.ALLOW)
        self.assertIn("explicitly allowed", decision.reason)

    def test_policy_error_fails_closed(self):
        class BrokenPolicy:
            policy_id = "broken"

            def evaluate(self, request):
                raise RuntimeError("policy store unavailable")

        decision = PermissionEngine(BrokenPolicy()).evaluate(self._request(Capability.PROJECT_READ))

        self.assertEqual(decision.decision, Decision.DENY)
        self.assertEqual(decision.error_code, ErrorCode.POLICY_EVALUATION_FAILED)


class AuditIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / ".env").write_text("API_TOKEN=plain-secret\n", encoding="utf-8")
        self.workspace = ProjectWorkspace.register(self.root)
        self.actor = Actor(ActorKind.EXTERNAL_AI, "audit-agent")
        self.audit = AuditLog(self.workspace, redactor=Redactor(["plain-secret"]))
        policy = StaticPermissionPolicy(
            {
                Capability.PROJECT_READ: Decision.ALLOW,
                Capability.PROJECT_WRITE: Decision.APPROVAL_REQUIRED,
            }
        )
        authorization = AuthorizationService(PermissionEngine(policy), self.audit)
        self.files = FileSystemService(self.workspace, authorization)

    def tearDown(self):
        self.temporary.cleanup()

    def test_allowed_operation_records_full_lifecycle_and_correlation(self):
        result = self.files.read_file(
            "src/app.py", actor=self.actor, request_id="read-1", task_id="task-1"
        )
        events = self.audit.events(request_id="read-1")

        self.assertTrue(result.success, result.to_dict())
        self.assertEqual(
            [event["status"] for event in events],
            ["REQUESTED", "AUTHORIZED", "STARTED", "COMPLETED"],
        )
        self.assertTrue(all(event["task_id"] == "task-1" for event in events))
        self.assertEqual(result.meta["request_id"], "read-1")
        self.assertEqual(result.meta["audit_event_id"], events[-1]["event_id"])

    def test_denied_and_approval_required_operations_are_audited(self):
        denied = self.files.read_file(".env", actor=self.actor, request_id="secret-1")
        pending = self.files.write_file(
            "src/app.py", "VALUE = 2\n", actor=self.actor, request_id="write-1"
        )

        self.assertEqual(denied.error.code, ErrorCode.SECRET_PATH_DENIED)
        self.assertEqual(pending.error.code, ErrorCode.PERMISSION_REQUIRED)
        self.assertEqual(
            [event["status"] for event in self.audit.events(request_id="secret-1")],
            ["REQUESTED", "DENIED"],
        )
        self.assertEqual(
            [event["status"] for event in self.audit.events(request_id="write-1")],
            ["REQUESTED", "APPROVAL_REQUIRED"],
        )

    def test_write_approval_is_bound_to_exact_content_digest(self):
        pending = self.files.write_file(
            "src/app.py", "VALUE = 2\n", actor=self.actor, request_id="write-exact"
        )
        grant = ApprovalGrant(
            request_id="write-exact",
            project_id=self.workspace.project_id,
            request_digest=pending.meta["permission_request_digest"],
            approved_by=Actor(ActorKind.USER, "owner"),
            note="Approved this exact file content.",
        )

        changed = self.files.write_file(
            "src/app.py", "VALUE = 3\n", actor=self.actor,
            request_id="write-exact", approval=grant,
        )
        approved = self.files.write_file(
            "src/app.py", "VALUE = 2\n", actor=self.actor,
            request_id="write-exact", approval=grant,
        )

        self.assertEqual(changed.error.code, ErrorCode.PERMISSION_DENIED)
        self.assertTrue(approved.success, approved.to_dict())
        self.assertEqual((self.root / "src" / "app.py").read_text(encoding="utf-8"), "VALUE = 2\n")

    def test_audit_redacts_sensitive_keys_inline_tokens_and_known_values(self):
        self.audit.record(
            actor=self.actor.to_dict(),
            operation="execution.run",
            capability=Capability.PROCESS_EXECUTE.value,
            resource="python",
            status=AuditStatus.REQUESTED,
            request_id="redact-1",
            metadata={
                "api_key": "plain-secret",
                "args": ["--token=plain-secret", "API_TOKEN=plain-secret"],
                "authorization": "Bearer plain-secret",
            },
        )

        raw = self.audit.path.read_text(encoding="utf-8")
        event = json.loads(raw.splitlines()[-1])
        self.assertNotIn("plain-secret", raw)
        self.assertEqual(event["metadata"]["api_key"], "[REDACTED]")
        self.assertIn("[REDACTED]", event["metadata"]["args"][0])


if __name__ == "__main__":
    unittest.main()
