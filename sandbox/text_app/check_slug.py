import unittest

from slug import slugify


class SlugTests(unittest.TestCase):
    def test_slugifies_titles_for_urls(self) -> None:
        self.assertEqual(slugify(" Hello, APOS 1.0! "), "hello-apos-1-0")


if __name__ == "__main__":
    unittest.main()
