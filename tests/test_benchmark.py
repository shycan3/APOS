import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from apos.benchmark import BenchmarkError, BenchmarkSuite, validate_benchmark_suite


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

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
