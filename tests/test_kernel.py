from pathlib import Path
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest

from apos.coder import build_coder_prompt
from apos.kernel import Kernel, RunOptions, _existing_permission_decision
from apos.models import ContextRequest, TaskSpec


class KernelTests(unittest.TestCase):
    def test_coder_prompt_defines_allowed_file_path_authority_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src" / "apos"
            source.mkdir(parents=True)
            (source / "mcp_server.py").write_text("from apos.mcp_server import build_server\n", encoding="utf-8")
            spec = TaskSpec.from_mapping(
                {
                    "task_id": "TASK-PATH-CONTRACT",
                    "goal": "Expose MCP tool names from apos.mcp_server.",
                    "allowed_files": ["src/apos/mcp_server.py"],
                    "test_commands": [
                        "python -c \"from apos.mcp_server import MCP_TOOL_NAMES\"",
                    ],
                }
            )

            prompt = json.loads(build_coder_prompt(root, spec, attempt=1))
            instructions = "\n".join(prompt["instructions"])

        self.assertIn("PATH AND WRITE AUTHORITY CONTRACT", instructions)
        self.assertIn("already approved for modification", instructions)
        self.assertIn("do not request write permission for a file listed in allowed_files", instructions)
        self.assertIn("repository-relative filesystem paths", instructions)
        self.assertIn("Use allowed file paths exactly as listed", instructions)
        self.assertIn("Python module names, import paths, and traceback module names are not repository filesystem paths", instructions)
        self.assertIn("Never convert a Python module/import name into a filesystem path", instructions)
        self.assertIn("Request permission only for a genuinely required repository-relative filesystem path outside allowed_files", instructions)
        self.assertIn("src/apos/mcp_server.py", prompt["task"]["allowed_files"])
        self.assertIn("src/apos/mcp_server.py", prompt["files"])

    def test_treats_existing_write_permission_request_as_granted(self):
        spec = TaskSpec.from_mapping(
            {
                "task_id": "TASK-PERMISSION",
                "goal": "Change app.",
                "allowed_files": ["app.py"],
                "test_commands": ["python -m unittest"],
            }
        )
        request = ContextRequest(type="read_file", path="app.py", permission="write", reason="Need write access.")

        self.assertEqual(_existing_permission_decision(request, spec), "write")

    def test_marks_task_passed_when_tests_already_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def greet(name):\n    return f\"Hello, {name}!\"\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import greet


                    class GreetingTests(unittest.TestCase):
                        def test_greet(self):
                            self.assertEqual(greet("APOS"), "Hello, APOS!")


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            spec = TaskSpec.from_mapping(
                {
                    "task_id": "TASK-PREFLIGHT",
                    "title": "Preflight",
                    "goal": "Make greet return a friendly message.",
                    "allowed_files": ["app.py"],
                    "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                }
            )

            summary = Kernel(root).run_task(spec, RunOptions(no_commit=True, command_timeout_seconds=30))

            self.assertEqual(summary.status, "PASS", summary.to_dict())
            self.assertEqual(summary.attempts[0].attempt, 0)
            self.assertEqual(summary.attempts[0].message, "tests already passed before coder changes")
            run_log = root / str(summary.run_log)
            self.assertTrue((run_log / "attempt-00" / "tests.json").exists())
            self.assertFalse((run_log / "attempt-00" / "prompt.json").exists())

    def test_runs_patch_test_loop_without_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import greet


                    class GreetingTests(unittest.TestCase):
                        def test_greet(self):
                            self.assertEqual(greet("APOS"), "Hello, APOS!")


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "fake_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import sys
                        sys.stdin.read()
                        print('''diff --git a/app.py b/app.py
                        --- a/app.py
                        +++ b/app.py
                        @@ -1,2 +1,2 @@
                         def greet(name):
                        -    return name
                        +    return f"Hello, {name}!"
                        '''.replace("                        ", ""), end="")
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )

                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-001",
                        "title": "Greeting",
                        "goal": "Make greet return a friendly message.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        allow_dirty=False,
                        command_timeout_seconds=30,
                    ),
                )

                self.assertEqual(summary.status, "PASS", summary.to_dict())
                self.assertIn('return f"Hello, {name}!"', (root / "app.py").read_text(encoding="utf-8"))
                self.assertIsNotNone(summary.run_log)

                run_log = root / str(summary.run_log)
                self.assertTrue((run_log / "run.json").exists())
                self.assertTrue((run_log / "task.json").exists())
                self.assertTrue((run_log / "attempt-01" / "prompt.json").exists())
                self.assertTrue((run_log / "attempt-01" / "response.patch").exists())
                self.assertTrue((run_log / "attempt-01" / "tests.json").exists())
                self.assertTrue((run_log / "summary.json").exists())

                summary_data = json.loads((run_log / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary_data["status"], "PASS")
                self.assertEqual(summary_data["run_log"], summary.run_log)

                status = self._git_status(root)
                self.assertNotIn(".apos/runs", status)

    def test_rolls_back_failed_attempt_before_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import greet


                    class GreetingTests(unittest.TestCase):
                        def test_greet(self):
                            self.assertEqual(greet("APOS"), "Hello, APOS!")


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "retry_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import json
                        import sys

                        prompt = json.loads(sys.stdin.read())
                        if prompt["attempt"] == 1:
                            replacement = '    return "Wrong"'
                        else:
                            replacement = '    return f"Hello, {name}!"'

                        print(f'''diff --git a/app.py b/app.py
                        --- a/app.py
                        +++ b/app.py
                        @@ -1,2 +1,2 @@
                         def greet(name):
                        -    return name
                        +{replacement}
                        '''.replace("                        ", ""), end="")
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )

                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-ROLLBACK",
                        "title": "Rollback",
                        "goal": "Make greet return a friendly message.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                        "max_attempts": 2,
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        command_timeout_seconds=30,
                    ),
                )

                self.assertEqual(summary.status, "PASS", summary.to_dict())
                self.assertEqual([attempt.status for attempt in summary.attempts], ["FAILED", "PASS"])
                self.assertIn('return f"Hello, {name}!"', (root / "app.py").read_text(encoding="utf-8"))

                run_log = root / str(summary.run_log)
                rollback = json.loads((run_log / "attempt-01" / "rollback.json").read_text(encoding="utf-8"))
                self.assertEqual(rollback["status"], "PASS")

    def test_applies_file_replacement_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import answer


                    class AnswerTests(unittest.TestCase):
                        def test_answer(self):
                            self.assertEqual(answer(), 42)


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "replacement_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import json

                        print(json.dumps({
                            "type": "file_replacement",
                            "path": "app.py",
                            "content": "def answer():\\n    return 42\\n",
                        }))
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )
                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-REPLACE",
                        "title": "Replace",
                        "goal": "Make answer return 42.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        command_timeout_seconds=30,
                    ),
                )

                self.assertEqual(summary.status, "PASS", summary.to_dict())
                self.assertEqual(summary.attempts[0].message, "tests passed after replacing app.py")
                self.assertIn("return 42", (root / "app.py").read_text(encoding="utf-8"))
                run_log = root / str(summary.run_log)
                response = json.loads((run_log / "attempt-01" / "response.json").read_text(encoding="utf-8"))
                self.assertEqual(response["type"], "file_replacement")
                self.assertTrue((run_log / "attempt-01" / "replacement.txt").exists())

    def test_rolls_back_failed_file_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import answer


                    class AnswerTests(unittest.TestCase):
                        def test_answer(self):
                            self.assertEqual(answer(), 42)


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "bad_replacement_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import json

                        print(json.dumps({
                            "type": "file_replacement",
                            "path": "app.py",
                            "content": "def answer():\\n    return -1\\n",
                        }))
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )
                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-REPLACE-FAIL",
                        "title": "Replace fail",
                        "goal": "Make answer return 42.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                        "max_attempts": 1,
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        command_timeout_seconds=30,
                    ),
                )

                self.assertEqual(summary.status, "FAILED", summary.to_dict())
                self.assertIn("return 0", (root / "app.py").read_text(encoding="utf-8"))
                rollback = json.loads((root / str(summary.run_log) / "attempt-01" / "rollback.json").read_text(encoding="utf-8"))
                self.assertEqual(rollback["status"], "PASS")

    def test_continues_after_preapproved_read_permission_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])

            (root / "app.py").write_text("def answer():\n    return 0\n", encoding="utf-8")
            (root / "helper.py").write_text("ANSWER = 42\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    from app import answer


                    class AnswerTests(unittest.TestCase):
                        def test_answer(self):
                            self.assertEqual(answer(), 42)


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "permission_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import json
                        import sys

                        prompt = json.loads(sys.stdin.read())
                        if prompt["attempt"] == 1:
                            print(json.dumps({
                                "type": "request_permission",
                                "permission": "read",
                                "path": "helper.py",
                                "reason": "Need the expected answer constant.",
                            }))
                        else:
                            assert "helper.py" in prompt["files"]
                            print('''diff --git a/app.py b/app.py
                        --- a/app.py
                        +++ b/app.py
                        @@ -1,2 +1,2 @@
                         def answer():
                        -    return 0
                        +    return 42
                        '''.replace("                        ", ""), end="")
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )

                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-PERMISSION",
                        "title": "Permission",
                        "goal": "Make answer return the expected value.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                        "max_attempts": 2,
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        command_timeout_seconds=30,
                        approved_read=("helper.py",),
                    ),
                )

                self.assertEqual(summary.status, "PASS", summary.to_dict())
                self.assertEqual([attempt.status for attempt in summary.attempts], ["PERMISSION_GRANTED", "PASS"])
                prompt = json.loads((root / str(summary.run_log) / "attempt-02" / "prompt.json").read_text(encoding="utf-8"))
                self.assertIn("helper.py", prompt["task"]["read_only_files"])
                self.assertIn("helper.py", prompt["files"])

    def test_stops_after_denied_permission_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                textwrap.dedent(
                    """
                    import unittest
                    import app


                    class ValueTests(unittest.TestCase):
                        def test_value(self):
                            self.assertEqual(app.value, 2)


                    if __name__ == "__main__":
                        unittest.main()
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "denied_coder.py"
                coder.write_text(
                    textwrap.dedent(
                        """
                        import json

                        print(json.dumps({
                            "type": "request_permission",
                            "permission": "read",
                            "path": "secret.py",
                            "reason": "Need extra context.",
                        }))
                        """
                    ).lstrip(),
                    encoding="utf-8",
                )
                spec = TaskSpec.from_mapping(
                    {
                        "task_id": "TASK-DENIED",
                        "goal": "Try to change app.",
                        "allowed_files": ["app.py"],
                        "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                    }
                )

                summary = Kernel(root).run_task(
                    spec,
                    RunOptions(
                        coder_command=f"{sys.executable} {coder}",
                        no_commit=True,
                        command_timeout_seconds=30,
                        denied_permissions=("secret.py",),
                    ),
                )

                self.assertEqual(summary.status, "PERMISSION_DENIED")
                self.assertEqual(summary.attempts[0].status, "PERMISSION_DENIED")

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> None:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)

    @staticmethod
    def _git_status(cwd: Path) -> str:
        completed = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
