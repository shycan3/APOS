import unittest

from todos import active_titles


class TodoTests(unittest.TestCase):
    def test_returns_active_titles_by_priority(self) -> None:
        items = [
            {"title": "low", "completed": False, "priority": 1},
            {"title": "done", "completed": True, "priority": 99},
            {"title": "high", "completed": False, "priority": 3},
        ]

        self.assertEqual(active_titles(items), ["high", "low"])


if __name__ == "__main__":
    unittest.main()
