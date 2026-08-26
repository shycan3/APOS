import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from apos.runlog import list_run_logs, load_run_log


class RunLogTests(unittest.TestCase):
    def test_lists_and_loads_run_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            run_dir = root / ".apos" / "runs" / "task-001" / "20260826T010000Z-abc12345"
            attempt_dir = run_dir / "attempt-01"
            attempt_dir.mkdir(parents=True)
            self._write_json(
                run_dir / "run.json",
                {
                    "task_id": "TASK-001",
                    "title": "Greeting",
                    "branch": "apos/task-001-greeting",
                    "started_at": "20260826T010000Z",
                },
            )
            self._write_json(run_dir / "task.json", {"task_id": "TASK-001", "goal": "Test"})
            self._write_json(
                attempt_dir / "attempt.json",
                {"attempt": 1, "status": "PASS", "message": "ok", "test_results": []},
            )
            self._write_json(
                run_dir / "summary.json",
                {
                    "status": "PASS",
                    "task_id": "TASK-001",
                    "branch": "apos/task-001-greeting",
                    "attempts": [{"attempt": 1, "status": "PASS", "message": "ok", "test_results": []}],
                    "committed": True,
                    "commit_hash": "abc1234",
                    "run_log": ".apos/runs/task-001/20260826T010000Z-abc12345",
                },
            )

            entries = list_run_logs(root)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].task_id, "TASK-001")
            self.assertEqual(entries[0].status, "PASS")
            self.assertEqual(entries[0].attempts, 1)

            detail = load_run_log(root, entries[0].relative_path)
            self.assertEqual(detail["path"], entries[0].relative_path)
            self.assertEqual(detail["summary"]["status"], "PASS")
            self.assertEqual(len(detail["attempts"]), 1)

            list_output = self._run(root, [sys.executable, "-m", "apos", "runs", "list"])
            self.assertIn("TASK-001", list_output.stdout)
            self.assertIn("abc1234", list_output.stdout)

            show_output = self._run(root, [sys.executable, "-m", "apos", "runs", "show", entries[0].relative_path])
            self.assertIn("실행 기록:", show_output.stdout)
            self.assertIn("시도 1: 통과(PASS)", show_output.stdout)

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
