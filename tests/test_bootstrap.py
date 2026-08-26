import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BootstrapCliTests(unittest.TestCase):
    def test_bootstrap_initializes_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])

            output = self._run(root, [sys.executable, "-m", "apos", "bootstrap"])

            self.assertIn("APOS 부트스트랩 완료.", output.stdout)
            self.assertIn("로컬 코더: <설정되지 않음>", output.stdout)
            self.assertTrue((root / ".apos" / "config.json").exists())
            self.assertTrue((root / ".apos" / "current.md").exists())

    def test_bootstrap_can_configure_ollama(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])

            output = self._run(
                root,
                [
                    sys.executable,
                    "-m",
                    "apos",
                    "bootstrap",
                    "--ollama-model",
                    "qwen-test:7b",
                    "--ollama-binary",
                    "C:\\Tools\\ollama.exe",
                ],
            )

            config = json.loads((root / ".apos" / "config.json").read_text(encoding="utf-8"))
            self.assertIn("qwen-test:7b", output.stdout)
            self.assertIn("apos.ollama", config["local_coder"]["command"])
            self.assertIn("--ollama-host", config["local_coder"]["command"])
            self.assertEqual(config["ollama"]["model"], "qwen-test:7b")
            self.assertEqual(config["ollama"]["binary"], "C:\\Tools\\ollama.exe")

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
