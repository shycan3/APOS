import unittest

from apos.models import SpecError, TaskSpec


class TaskSpecTests(unittest.TestCase):
    def test_loads_valid_task_spec(self):
        spec = TaskSpec.from_mapping(
            {
                "task_id": "TASK-001",
                "goal": "Change a file",
                "allowed_files": ["src/app.py"],
                "test_commands": ["python -m unittest"],
            }
        )

        self.assertEqual(spec.task_id, "TASK-001")
        self.assertEqual(spec.allowed_files, ["src/app.py"])

    def test_rejects_missing_allowed_files(self):
        with self.assertRaises(SpecError):
            TaskSpec.from_mapping(
                {
                    "task_id": "TASK-001",
                    "goal": "Change a file",
                    "test_commands": ["python -m unittest"],
                }
            )


if __name__ == "__main__":
    unittest.main()

