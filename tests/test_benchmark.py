import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from apos.benchmark import (
    BenchmarkError,
    BenchmarkRunOptions,
    BenchmarkSuite,
    compare_benchmark_results,
    list_benchmark_results,
    load_benchmark_result,
    run_benchmark_suite,
    validate_benchmark_suite,
)


class BenchmarkSuiteTests(unittest.TestCase):
    def test_loads_valid_suite(self):
        suite = BenchmarkSuite.from_mapping(
            {
                "suite_id": "suite-1",
                "title": "Suite",
                "tasks": [
                    {
                        "task_id": "TASK-001",
                        "path": "tasks/task-001.json",
                        "category": "code-change",
                        "difficulty": "easy",
                        "tags": ["python"],
                    }
                ],
            }
        )

        self.assertEqual(suite.suite_id, "suite-1")
        self.assertEqual(suite.tasks[0].weight, 1)
        self.assertEqual(suite.metrics, ["quality.score", "attempts", "tests.failed"])

    def test_rejects_duplicate_task_id(self):
        with self.assertRaises(BenchmarkError):
            BenchmarkSuite.from_mapping(
                {
                    "suite_id": "suite-1",
                    "title": "Suite",
                    "tasks": [
                        {"task_id": "TASK-001", "path": "tasks/a.json"},
                        {"task_id": "TASK-001", "path": "tasks/b.json"},
                    ],
                }
            )

    def test_validates_taskspec_paths_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            (root / "tasks").mkdir()
            (root / "benchmarks").mkdir()
            self._write_json(
                root / "tasks" / "task-001.json",
                {
                    "task_id": "TASK-001",
                    "goal": "Change app.py",
                    "allowed_files": ["app.py"],
                    "test_commands": ["python -m unittest"],
                },
            )
            self._write_json(
                root / "benchmarks" / "suite.json",
                {
                    "suite_id": "suite-1",
                    "title": "Suite",
                    "tasks": [{"task_id": "TASK-001", "path": "tasks/task-001.json"}],
                },
            )

            suite = validate_benchmark_suite(root, root / "benchmarks" / "suite.json")
            self.assertEqual(suite.tasks[0].task_id, "TASK-001")

            validate_output = self._run(root, [sys.executable, "-m", "apos", "benchmark", "validate", "benchmarks/suite.json"])
            self.assertIn("Benchmark suite valid: suite-1", validate_output.stdout)

            show_output = self._run(root, [sys.executable, "-m", "apos", "benchmark", "show", "benchmarks/suite.json"])
            self.assertIn("Benchmark suite: suite-1", show_output.stdout)
            self.assertIn("TASK-001", show_output.stdout)

            json_output = self._run(root, [sys.executable, "-m", "apos", "benchmark", "show", "benchmarks/suite.json", "--json"])
            self.assertEqual(json.loads(json_output.stdout)["suite_id"], "suite-1")

    def test_runs_benchmark_suite_and_writes_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            self._run(root, ["git", "config", "user.email", "apos@example.test"])
            self._run(root, ["git", "config", "user.name", "APOS Test"])
            (root / "tasks").mkdir()
            (root / "benchmarks").mkdir()
            (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            (root / "app.py").write_text("def greet(name):\n    return name\n", encoding="utf-8")
            (root / "test_app.py").write_text(
                "\n".join(
                    [
                        "import unittest",
                        "from app import greet",
                        "",
                        "",
                        "class GreetingTests(unittest.TestCase):",
                        "    def test_greet(self):",
                        "        self.assertEqual(greet('APOS'), 'Hello, APOS!')",
                        "",
                        "",
                        "if __name__ == '__main__':",
                        "    unittest.main()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            self._write_json(
                root / "tasks" / "task-001.json",
                {
                    "task_id": "TASK-001",
                    "title": "Greeting",
                    "goal": "Make greet return a friendly message.",
                    "allowed_files": ["app.py"],
                    "test_commands": [f"{sys.executable} -m unittest test_app.py"],
                },
            )
            self._write_json(
                root / "benchmarks" / "suite.json",
                {
                    "suite_id": "suite-1",
                    "title": "Suite",
                    "tasks": [{"task_id": "TASK-001", "path": "tasks/task-001.json"}],
                },
            )
            self._run(root, ["git", "add", "."])
            self._run(root, ["git", "commit", "-m", "initial"])

            with tempfile.TemporaryDirectory() as coder_tmp:
                coder = Path(coder_tmp) / "fake_coder.py"
                coder.write_text(
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
''', end="")
""".lstrip(),
                    encoding="utf-8",
                )

                result = run_benchmark_suite(
                    root,
                    root / "benchmarks" / "suite.json",
                    BenchmarkRunOptions(coder_command=f"{sys.executable} {coder}", command_timeout_seconds=30),
                )

                self.assertEqual(result["status"], "PASS", result)
                self.assertEqual(result["summary"]["passed_tasks"], 1)
                self.assertEqual(result["runner_profile"]["apos_version"], "0.1.0")
                self.assertIn("fake_coder.py", result["runner_profile"]["coder_command"])
                self.assertEqual(result["tasks"][0]["report"]["quality"]["verdict"], "ready")
                self.assertTrue((root / str(result["result_path"])).exists())

                entries = list_benchmark_results(root)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].suite_id, "suite-1")
                self.assertEqual(entries[0].passed_tasks, 1)

                loaded = load_benchmark_result(root, str(result["result_path"]))
                self.assertEqual(loaded["result_id"], result["result_id"])

                list_output = self._run(root, [sys.executable, "-m", "apos", "benchmark", "results", "list"])
                self.assertIn("suite-1", list_output.stdout)
                self.assertIn("tasks=1/1", list_output.stdout)

                show_output = self._run(
                    root,
                    [sys.executable, "-m", "apos", "benchmark", "results", "show", str(result["result_path"])],
                )
                self.assertIn("Benchmark result:", show_output.stdout)
                self.assertIn("Runner: APOS", show_output.stdout)
                self.assertIn("TASK-001", show_output.stdout)

    def test_compares_benchmark_results_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            result_a = root / ".apos" / "benchmarks" / "suite-1" / "run-a" / "result.json"
            result_b = root / ".apos" / "benchmarks" / "suite-1" / "run-b" / "result.json"
            result_a.parent.mkdir(parents=True)
            result_b.parent.mkdir(parents=True)
            self._write_benchmark_result(result_a, "suite-1", "run-a", passed=1, total=2, score=70, duration=12.5, model="model-a")
            self._write_benchmark_result(result_b, "suite-1", "run-b", passed=2, total=2, score=92, duration=9.25, model="model-b")

            comparison = compare_benchmark_results(root, [str(result_a.relative_to(root)), str(result_b.relative_to(root))])

            self.assertEqual(comparison["summary"]["result_count"], 2)
            self.assertEqual(comparison["summary"]["best_result_id"], "run-b")
            self.assertEqual(comparison["results"][0]["rank"], 1)
            self.assertEqual(comparison["results"][0]["result_id"], "run-b")
            self.assertEqual(comparison["results"][0]["pass_rate"], 1.0)
            self.assertEqual(comparison["results"][0]["total_duration_seconds"], 9.25)

            output = self._run(
                root,
                [
                    sys.executable,
                    "-m",
                    "apos",
                    "benchmark",
                    "compare",
                    str(result_a.relative_to(root)),
                    str(result_b.relative_to(root)),
                ],
            )
            self.assertIn("Benchmark comparison", output.stdout)
            self.assertIn("#1  run-b", output.stdout)
            self.assertIn("model=model-b", output.stdout)

            json_output = self._run(
                root,
                [
                    sys.executable,
                    "-m",
                    "apos",
                    "benchmark",
                    "compare",
                    str(result_a.relative_to(root)),
                    str(result_b.relative_to(root)),
                    "--json",
                ],
            )
            self.assertEqual(json.loads(json_output.stdout)["summary"]["best_result_id"], "run-b")

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def _write_benchmark_result(
        cls,
        path: Path,
        suite_id: str,
        result_id: str,
        passed: int,
        total: int,
        score: int,
        duration: float,
        model: str,
    ) -> None:
        cls._write_json(
            path,
            {
                "suite": {"suite_id": suite_id, "title": "Suite"},
                "runner_profile": {
                    "apos_version": "0.1.0",
                    "coder_command": f"coder-for-{model}",
                    "ollama": {"model": model},
                },
                "started_at": "20260826T010000Z",
                "result_id": result_id,
                "status": "PASS" if passed == total else "FAILED",
                "tasks": [{"duration_seconds": duration}],
                "summary": {
                    "total_tasks": total,
                    "completed_tasks": total,
                    "passed_tasks": passed,
                    "failed_tasks": total - passed,
                    "average_quality_score": score,
                },
            },
        )

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
