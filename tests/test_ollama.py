import json
import unittest

from apos.ollama import build_model_prompt, extract_protocol_output


class OllamaAdapterTests(unittest.TestCase):
    def test_extracts_raw_diff(self):
        output = """Here is the patch:
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
"""

        self.assertEqual(
            extract_protocol_output(output),
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
        )

    def test_extracts_fenced_diff(self):
        output = """```diff
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
```"""

        extracted = extract_protocol_output(output)

        self.assertTrue(extracted.startswith("diff --git a/app.py b/app.py"))
        self.assertNotIn("```", extracted)

    def test_extracts_permission_request_json(self):
        request = {
            "type": "request_permission",
            "path": "src/config.py",
            "permission": "read",
            "reason": "Need config defaults.",
        }

        self.assertEqual(json.loads(extract_protocol_output(json.dumps(request))), request)

    def test_recovers_fenced_permission_request_with_terminal_controls(self):
        output = """```json
{
  "type": "request_permission",
  "path": "sandbox/text_app/slug.py",
  "permission": "write",
  "reason": "Need write access after a failed patch.\x1b[3D\x1b[K
Please approve."
}
```"""

        extracted = json.loads(extract_protocol_output(output))

        self.assertEqual(extracted["type"], "request_permission")
        self.assertEqual(extracted["path"], "sandbox/text_app/slug.py")
        self.assertEqual(extracted["permission"], "write")
        self.assertIn("Need write access", extracted["reason"])

    def test_model_prompt_contains_protocol(self):
        prompt = build_model_prompt('{"protocol":"APOS_LOCAL_CODER_PATCH_V1"}')

        self.assertIn("APOS Local Coder", prompt)
        self.assertIn("APOS_LOCAL_CODER_PATCH_V1", prompt)


if __name__ == "__main__":
    unittest.main()
