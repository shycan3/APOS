import json
import unittest
from unittest.mock import patch

from apos.ollama import (
    APOS_PROTOCOL_RESPONSE_SCHEMA,
    build_model_prompt,
    build_protocol_repair_prompt,
    extract_protocol_output,
    repair_ollama_protocol_output,
    run_ollama,
    run_ollama_protocol,
)


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

    def test_rejects_context_diff(self):
        output = """```
1c1
< old
---
> new
```"""

        self.assertEqual(extract_protocol_output(output), "")

    def test_extracts_permission_request_json(self):
        request = {
            "type": "request_permission",
            "path": "src/config.py",
            "permission": "read",
            "reason": "Need config defaults.",
        }

        self.assertEqual(json.loads(extract_protocol_output(json.dumps(request))), request)

    def test_rejects_incomplete_json_protocol_objects(self):
        for payload in (
            {"type": "patch", "content": "diff --git a/app.py b/app.py\n"},
            {"type": "file_replacement", "path": "app.py"},
            {"type": "request_permission", "reason": "need context"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(extract_protocol_output(json.dumps(payload)), "")

    def test_extracts_file_replacement_json(self):
        replacement = {
            "type": "file_replacement",
            "path": "app.py",
            "content": "def answer():\n    return 42\n",
        }

        self.assertEqual(json.loads(extract_protocol_output(json.dumps(replacement))), replacement)

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

    def test_recovers_malformed_fenced_file_replacement(self):
        output = """```json
{
  "type": "file_replacement",
  "path": "sandbox/todo_app/todos.py",
  "content": "def active_titles(items: list[dict[str, object]]) -> list[str\x1b[8D\x1b[K
list[str]:\\n    return [str(item[\\\"title\\\"]) for item in items if not item[\\\"completed\\\"]]"
}
```"""

        extracted = json.loads(extract_protocol_output(output))

        self.assertEqual(extracted["type"], "file_replacement")
        self.assertEqual(extracted["path"], "sandbox/todo_app/todos.py")
        self.assertIn("active_titles", extracted["content"])

    def test_model_prompt_contains_protocol(self):
        prompt = build_model_prompt('{"protocol":"APOS_LOCAL_CODER_PATCH_V1"}')

        self.assertIn("APOS Local Coder", prompt)
        self.assertIn("APOS_LOCAL_CODER_PATCH_V1", prompt)

    def test_protocol_repair_prompt_rejects_code_blocks_and_context_diffs(self):
        prompt = build_protocol_repair_prompt(
            '{"task":{"allowed_files":["app.py"]}}',
            "Here is code:\n```python\nprint('hi')\n```",
        )

        self.assertIn("Return only one JSON object", prompt)
        self.assertIn('must contain a "type" key', prompt)
        self.assertIn('Do not return an object with a "response"', prompt)
        self.assertIn("Do not return Python", prompt)
        self.assertIn("Do not return context diffs", prompt)
        self.assertIn("task.allowed_files", prompt)

    def test_run_ollama_prefers_http_generate(self):
        with patch("apos.ollama.run_ollama_generate", return_value="patch") as generate:
            with patch("apos.ollama.run_ollama_prompt") as prompt:
                output = run_ollama(
                    model="qwen-test",
                    apos_prompt='{"task":{}}',
                    ollama_binary="ollama",
                    ollama_host="http://127.0.0.1:11434",
                    timeout_seconds=10,
                )

        self.assertEqual(output, "patch")
        generate.assert_called_once()
        prompt.assert_not_called()

    def test_run_ollama_falls_back_to_cli(self):
        with patch("apos.ollama.run_ollama_generate", side_effect=RuntimeError("down")):
            with patch("apos.ollama.run_ollama_prompt", return_value="fallback") as prompt:
                output = run_ollama(
                    model="qwen-test",
                    apos_prompt='{"task":{}}',
                    ollama_binary="ollama",
                    ollama_host="http://127.0.0.1:11434",
                    timeout_seconds=10,
                )

        self.assertEqual(output, "fallback")
        prompt.assert_called_once()

    def test_protocol_run_repairs_prose_code_block_once(self):
        invalid = """Here is the implementation:
```python
def answer():
    return 42
```"""
        repaired = json.dumps(
            {
                "type": "patch",
                "patch": "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
            }
        )
        with patch("apos.ollama.run_ollama", return_value=invalid) as first:
            with patch("apos.ollama.repair_ollama_protocol_output", return_value=repaired) as repair:
                result = run_ollama_protocol(
                    model="qwen-test",
                    apos_prompt='{"task":{"allowed_files":["app.py"]}}',
                    ollama_binary="ollama",
                    ollama_host="http://127.0.0.1:11434",
                    timeout_seconds=10,
                )

        self.assertEqual(json.loads(result.protocol_output)["type"], "patch")
        first.assert_called_once()
        repair.assert_called_once()

    def test_protocol_run_does_not_repair_valid_output(self):
        valid = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        with patch("apos.ollama.run_ollama", return_value=valid):
            with patch("apos.ollama.repair_ollama_protocol_output") as repair:
                result = run_ollama_protocol(
                    model="qwen-test",
                    apos_prompt='{"task":{}}',
                    ollama_binary="ollama",
                    ollama_host="http://127.0.0.1:11434",
                    timeout_seconds=10,
                )

        self.assertEqual(result.protocol_output, valid)
        repair.assert_not_called()

    def test_protocol_run_fails_safely_when_repair_is_invalid(self):
        with patch("apos.ollama.run_ollama", return_value="Here is prose."):
            with patch("apos.ollama.repair_ollama_protocol_output", return_value="Still prose.") as repair:
                result = run_ollama_protocol(
                    model="qwen-test",
                    apos_prompt='{"task":{}}',
                    ollama_binary="ollama",
                    ollama_host="http://127.0.0.1:11434",
                    timeout_seconds=10,
                )

        self.assertEqual(result.protocol_output, "")
        repair.assert_called_once()

    def test_protocol_repair_requests_json_format_when_http_is_available(self):
        with patch("apos.ollama.run_ollama_generate", return_value='{"type":"request_permission","path":"app.py"}') as generate:
            output = repair_ollama_protocol_output(
                model="qwen-test",
                apos_prompt='{"task":{}}',
                invalid_output="prose",
                ollama_binary="ollama",
                ollama_host="http://127.0.0.1:11434",
                timeout_seconds=10,
            )

        self.assertIn("request_permission", output)
        self.assertEqual(generate.call_args.kwargs["json_format"], APOS_PROTOCOL_RESPONSE_SCHEMA)

    def test_generate_accepts_json_schema_format(self):
        schema = {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"type": {"const": "request_permission"}, "path": {"type": "string"}},
                    "required": ["type", "path"],
                }
            ]
        }
        with patch("apos.ollama.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
                {"response": '{"type":"request_permission","path":"app.py"}'}
            ).encode("utf-8")

            output = __import__("apos.ollama").ollama.run_ollama_generate(
                model="qwen-test",
                prompt="return JSON",
                ollama_host="http://127.0.0.1:11434",
                timeout_seconds=10,
                json_format=schema,
            )

        self.assertIn("request_permission", output)
        sent = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["format"], schema)


if __name__ == "__main__":
    unittest.main()
