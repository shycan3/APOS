import json
import unittest

from apos.coder import parse_coder_output


class CoderProtocolTests(unittest.TestCase):
    def test_parses_file_replacement_response(self):
        response = parse_coder_output(
            json.dumps(
                {
                    "type": "file_replacement",
                    "path": "app.py",
                    "content": "def answer():\n    return 42\n",
                }
            )
        )

        self.assertEqual(response.type, "file_replacement")
        self.assertEqual(response.path, "app.py")
        self.assertIn("return 42", response.content)


if __name__ == "__main__":
    unittest.main()
