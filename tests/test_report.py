import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from apos.report import build_quality_report, generate_quality_report


class QualityReportTests(unittest.TestCase):
    def test_builds_report_from_successful_run(self):
        report = build_quality_report(
            {
                "path": ".apos/runs/task-001/run-1",
                "run": {"task_id": "TASK-001", "title": "Greeting", "branch": "apos/task-001", "started_at": "now"},
                "task": {"task_id": "TASK-001"},
                "summary": {
                    "status": "PASS",
                    "task_id": "TASK-001",
                    "branch": "apos/task-001",
                    "attempts": [{"attempt": 1, "status": "PASS"}],
                    "committed": True,
                    "commit_hash": "abc1234",
                },
                "attempts": [
                    {
                        "result": {"attempt": 1, "status": "PASS", "message": "ok"},
                        "response": {"type": "patch"},
                        "tests": [{"status": "PASS", "exit_code": 0}],
                    }
                ],
            }
        )

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["quality"]["verdict"], "ready")
        self.assertEqual(report["quality"]["score"], 100)
        self.assertEqual(report["tests"]["passed"], 1)
        self.assertEqual(report["failure"]["primary"], "none")
        self.assertFalse(report["failure"]["recovered"])

    def test_reports_retry_penalty_and_rollback(self):
        report = build_quality_report(
            {
                "path": ".apos/runs/task-rollback/run-1",
                "run": {"task_id": "TASK-ROLLBACK", "title": "Rollback", "branch": "apos/task-rollback"},
                "task": {"task_id": "TASK-ROLLBACK"},
                "summary": {
                    "status": "PASS",
                    "task_id": "TASK-ROLLBACK",
                    "branch": "apos/task-rollback",
                    "attempts": [{"attempt": 1, "status": "FAILED"}, {"attempt": 2, "status": "PASS"}],
                    "committed": False,
                },
                "attempts": [
                    {
                        "result": {"attempt": 1, "status": "FAILED", "message": "test failed"},
                        "response": {"type": "patch"},
                        "tests": [{"status": "FAILED", "exit_code": 1}],
                        "rollback": {"status": "PASS", "message": "rolled back"},
                    },
                    {
                        "result": {"attempt": 2, "status": "PASS", "message": "ok"},
                        "response": {"type": "patch"},
                        "tests": [{"status": "PASS", "exit_code": 0}],
                    },
                ],
            }
        )

        self.assertEqual(report["quality"]["verdict"], "usable")
        self.assertEqual(report["quality"]["score"], 70)
        self.assertEqual(report["rollbacks"]["passed"], 1)
        self.assertEqual(report["failure"]["primary"], "recovered")
        self.assertTrue(report["failure"]["recovered"])
        self.assertEqual(report["failure"]["reasons"][0]["code"], "verification_failed")

    def test_counts_file_replacement_responses(self):
        report = build_quality_report(
            {
                "path": ".apos/runs/task-replace/run-1",
                "run": {"task_id": "TASK-REPLACE", "title": "Replace", "branch": "apos/task-replace"},
                "task": {"task_id": "TASK-REPLACE"},
                "summary": {
                    "status": "PASS",
                    "task_id": "TASK-REPLACE",
                    "branch": "apos/task-replace",
                    "attempts": [{"attempt": 1, "status": "PASS"}],
                    "committed": True,
                },
                "attempts": [
                    {
                        "result": {"attempt": 1, "status": "PASS", "message": "ok"},
                        "response": {"type": "file_replacement", "path": "app.py"},
                        "tests": [{"status": "PASS", "exit_code": 0}],
                    }
                ],
            }
        )

        self.assertEqual(report["responses"]["file_replacement"], 1)
        self.assertEqual(report["failure"]["primary"], "none")

    def test_classifies_permission_blocked_runs(self):
        report = build_quality_report(
            {
                "path": ".apos/runs/task-permission/run-1",
                "run": {"task_id": "TASK-PERMISSION", "title": "Permission", "branch": "apos/task-permission"},
                "task": {"task_id": "TASK-PERMISSION"},
                "summary": {
                    "status": "NEEDS_PERMISSION",
                    "task_id": "TASK-PERMISSION",
                    "branch": "apos/task-permission",
                    "attempts": [{"attempt": 1, "status": "NEEDS_PERMISSION"}],
                    "committed": False,
                },
                "attempts": [
                    {
                        "result": {
                            "attempt": 1,
                            "status": "NEEDS_PERMISSION",
                            "message": "Local Coder requested permission: read src/app/config.py",
                        },
                        "response": {"type": "request_permission"},
                        "tests": [],
                    },
                ],
            }
        )

        self.assertEqual(report["quality"]["verdict"], "blocked")
        self.assertEqual(report["failure"]["primary"], "permission_required")
        self.assertEqual(report["failure"]["reasons"][0]["code"], "permission_required")

    def test_cli_generates_report_for_run_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            run_dir = root / ".apos" / "runs" / "task-001" / "20260826T010000Z-abc12345"
            attempt_dir = run_dir / "attempt-01"
            attempt_dir.mkdir(parents=True)
            self._write_json(run_dir / "run.json", {"task_id": "TASK-001", "title": "Greeting", "branch": "apos/task-001"})
            self._write_json(run_dir / "task.json", {"task_id": "TASK-001"})
            self._write_json(attempt_dir / "attempt.json", {"attempt": 1, "status": "PASS", "message": "ok"})
            self._write_json(attempt_dir / "response.json", {"type": "patch"})
            self._write_json(attempt_dir / "tests.json", [{"status": "PASS", "exit_code": 0}])
            self._write_json(
                run_dir / "summary.json",
                {
                    "status": "PASS",
                    "task_id": "TASK-001",
                    "branch": "apos/task-001",
                    "attempts": [{"attempt": 1, "status": "PASS"}],
                    "committed": True,
                    "commit_hash": "abc1234",
                },
            )

            report = generate_quality_report(root, ".apos/runs/task-001/20260826T010000Z-abc12345")
            self.assertEqual(report["quality"]["verdict"], "ready")

            output = self._run(root, [sys.executable, "-m", "apos", "report", ".apos/runs/task-001/20260826T010000Z-abc12345"])
            self.assertIn("품질 보고서:", output.stdout)
            self.assertIn("판정: 준비 완료(ready)", output.stdout)

            json_output = self._run(
                root,
                [sys.executable, "-m", "apos", "report", ".apos/runs/task-001/20260826T010000Z-abc12345", "--json"],
            )
            self.assertEqual(json.loads(json_output.stdout)["quality"]["score"], 100)

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
