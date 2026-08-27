from pathlib import Path
import tempfile
import unittest

import apos.core as core_api
from apos.core import (
    Actor,
    ActorKind,
    Capability,
    CommandPolicy,
    Decision,
    ErrorCode,
    ProjectRuntime,
    StaticPermissionPolicy,
    TaskService,
)


class ProjectRuntimeTests(unittest.TestCase):
    def test_composes_one_project_scoped_runtime_and_tool_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = ProjectRuntime.create(
                root,
                permission_policy=StaticPermissionPolicy(
                    {Capability.PROJECT_READ: Decision.ALLOW}
                ),
                command_policy=CommandPolicy.current_python(),
            )
            actor = Actor(ActorKind.EXTERNAL_AI, "runtime-test-agent")

            result = runtime.filesystem.read_file("app.py", actor=actor)
            tools = {definition.name: definition for definition in runtime.tools.list()}

            self.assertTrue(result.success, result.to_dict())
            self.assertEqual(runtime.workspace.project_id, runtime.audit_log.workspace.project_id)
            self.assertIsInstance(runtime.tasks, TaskService)
            self.assertIs(runtime.validation.filesystem, runtime.filesystem)
            self.assertFalse(hasattr(runtime, "task_repository"))
            self.assertFalse(hasattr(runtime.tasks, "repository"))
            self.assertFalse(hasattr(core_api, "TaskRepository"))
            self.assertFalse(hasattr(core_api, "SQLiteTaskRepository"))
            self.assertEqual(tools["filesystem.read"].capability, Capability.PROJECT_READ)
            self.assertEqual(tools["execution.run"].capability, Capability.PROCESS_EXECUTE)
            self.assertTrue(all(definition.project_scoped for definition in tools.values()))

    def test_runtime_requires_explicit_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TypeError):
                ProjectRuntime.create(Path(tmp))

    def test_read_only_profile_allows_project_read_and_denies_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            runtime = ProjectRuntime.create_read_only(root)
            actor = Actor(ActorKind.USER, "local-cli")

            read_result = runtime.filesystem.read_file("app.py", actor=actor)
            write_result = runtime.filesystem.write_file("app.py", "VALUE = 2\n", actor=actor)

            self.assertTrue(read_result.success, read_result.to_dict())
            self.assertEqual(write_result.error.code, ErrorCode.PERMISSION_DENIED)
            self.assertEqual((root / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")
            self.assertEqual(runtime.permission_engine.policy.policy_id, "production-project-read-v1")


if __name__ == "__main__":
    unittest.main()
