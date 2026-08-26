import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from apos.draft import DraftError, draft_task_spec, extract_task_spec_json, next_task_id, refine_task_spec_with_ollama, title_from_goal
from apos.models import TaskSpec


class DraftTests(unittest.TestCase):
    def test_generates_valid_task_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = draft_task_spec(
                root=root,
                goal="Add greeting behavior",
                allowed_files=["app.py"],
                test_commands=["python -m unittest"],
            )

            self.assertEqual(spec.task_id, "TASK-001")
            self.assertEqual(spec.title, "Add greeting behavior")
            self.assertEqual(spec.allowed_files, ["app.py"])
            self.assertEqual(spec.max_attempts, 3)

    def test_next_task_id_reads_existing_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            (root / "tasks" / "task-002.json").write_text('{"task_id":"TASK-002"}', encoding="utf-8")
            (root / "tasks" / "task-010.json").write_text('{"task_id":"TASK-010"}', encoding="utf-8")

            self.assertEqual(next_task_id(root), "TASK-011")

    def test_title_from_goal_is_short(self):
        self.assertEqual(title_from_goal("Add compact reports for benchmark runs please"), "Add compact reports for benchmark runs please")

    def test_cli_writes_task_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            output = self._run(
                root,
                [
                    sys.executable,
                    "-m",
                    "apos",
                    "draft",
                    "Add greeting behavior",
                    "--allow",
                    "app.py",
                    "--test",
                    "python -m unittest",
                    "--output",
                    "tasks/task-001.json",
                ],
            )

            self.assertIn("TaskSpec 저장 완료: tasks", output.stdout)
            spec = TaskSpec.load(root / "tasks" / "task-001.json")
            self.assertEqual(spec.task_id, "TASK-001")
            self.assertEqual(spec.expected_behavior, ["Add greeting behavior"])

            data = json.loads((root / "tasks" / "task-001.json").read_text(encoding="utf-8"))
            self.assertEqual(data["allowed_files"], ["app.py"])

            json_output = self._run(
                root,
                [
                    sys.executable,
                    "-m",
                    "apos",
                    "draft",
                    "Add another behavior",
                    "--allow",
                    "app.py",
                    "--test",
                    "python -m unittest",
                    "--output",
                    "tasks/task-002.json",
                    "--json",
                ],
            )
            self.assertEqual(json.loads(json_output.stdout)["path"], "tasks/task-002.json")

    def test_extracts_fenced_task_spec_json(self):
        output = """```json
{
  "task_id": "TASK-001",
  "goal": "Add behavior",
  "allowed_files": ["app.py"],
  "test_commands": ["python -m unittest"]
}
```"""

        self.assertEqual(extract_task_spec_json(output)["task_id"], "TASK-001")

    def test_refines_task_spec_with_ollama(self):
        spec = TaskSpec.from_mapping(
            {
                "task_id": "TASK-001",
                "goal": "Add behavior",
                "allowed_files": ["app.py"],
                "test_commands": ["python -m unittest"],
                "max_attempts": 3,
            }
        )
        refined_json = {
            **spec.to_dict(),
            "title": "Add behavior",
            "constraints": ["Keep the public API stable."],
            "expected_behavior": ["The behavior is observable through tests."],
        }

        with patch("apos.draft.run_ollama_prompt", return_value=json.dumps(refined_json)):
            refined = refine_task_spec_with_ollama(spec, model="test-model", ollama_binary="ollama", timeout_seconds=10)

        self.assertEqual(refined.task_id, "TASK-001")
        self.assertEqual(refined.constraints, ["Keep the public API stable."])

    def test_rejects_refinement_that_changes_preserved_fields(self):
        spec = TaskSpec.from_mapping(
            {
                "task_id": "TASK-001",
                "goal": "Add behavior",
                "allowed_files": ["app.py"],
                "test_commands": ["python -m unittest"],
            }
        )
        changed = {**spec.to_dict(), "allowed_files": ["other.py"]}

        with patch("apos.draft.run_ollama_prompt", return_value=json.dumps(changed)):
            with self.assertRaises(DraftError):
                refine_task_spec_with_ollama(spec, model="test-model", ollama_binary="ollama", timeout_seconds=10)

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
