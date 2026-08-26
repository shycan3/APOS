import unittest

from greeting import greet


class GreetingTests(unittest.TestCase):
    def test_greet_formats_message(self) -> None:
        self.assertEqual(greet("APOS"), "Hello, APOS!")


if __name__ == "__main__":
    unittest.main()
