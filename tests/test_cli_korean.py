import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class KoreanCliTests(unittest.TestCase):
    def test_main_help_is_korean(self):
        output = self._run(Path.cwd(), [sys.executable, "-m", "apos", "--help"])

        self.assertIn("사용법:", output.stdout)
        self.assertIn("위치 인수:", output.stdout)
        self.assertIn("선택 사항:", output.stdout)
        self.assertIn("자기진화 실행 환경", output.stdout)
        self.assertNotIn("positional arguments:", output.stdout)

    def test_evolution_help_is_korean(self):
        output = self._run(Path.cwd(), [sys.executable, "-m", "apos", "evolution", "--help"])

        self.assertIn("격리된 후보 worktree를 생성합니다", output.stdout)
        self.assertIn("신뢰된 1.1 기준으로 후보를 평가합니다", output.stdout)

    def test_status_output_is_korean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, ["git", "init"])
            output = self._run(root, [sys.executable, "-m", "apos", "status"])

            self.assertIn("프로젝트:", output.stdout)
            self.assertIn("브랜치:", output.stdout)
            self.assertIn("로컬 코더:", output.stdout)
            self.assertIn("Git 상태: 깨끗함", output.stdout)

    def test_common_parser_error_is_korean(self):
        output = self._run(
            Path.cwd(),
            [sys.executable, "-m", "apos", "evolution", "review"],
            expected_returncode=2,
        )

        self.assertIn("오류: 다음 인수가 필요합니다:", output.stderr)
        self.assertIn("사용법:", output.stderr)

        invalid = self._run(
            Path.cwd(),
            [
                sys.executable,
                "-m",
                "apos",
                "evolution",
                "review",
                "candidate",
                "--reviewer",
                "unknown",
                "--decision",
                "approve",
                "--note",
                "test",
            ],
            expected_returncode=2,
        )
        self.assertIn("잘못된 선택:", invalid.stderr)

    def test_json_output_contract_is_unchanged(self):
        output = self._run(Path.cwd(), [sys.executable, "-m", "apos", "task-template"])
        payload = json.loads(output.stdout)

        self.assertEqual(payload["task_id"], "TASK-001")
        self.assertIn("allowed_files", payload)
        self.assertIn("test_commands", payload)

    def test_version_reports_candidate_version(self):
        output = self._run(Path.cwd(), [sys.executable, "-m", "apos", "--version"])
        self.assertEqual(output.stdout.strip(), "apos 1.2.0")

    def test_apos_no_args_non_interactive_shows_help(self):
        output = self._run(Path.cwd(), [sys.executable, "-m", "apos"])
        self.assertIn("사용법:", output.stdout)
        self.assertIn("APOS 1.2", output.stdout)

    def test_evolution_no_args_non_interactive_shows_status(self):
        output = self._run(Path.cwd(), [sys.executable, "-m", "apos", "evolution"])
        self.assertIn("진화 상태:", output.stdout)
        self.assertIn("기준선: v1.1.0", output.stdout)

    @staticmethod
    def _run(
        cwd: Path,
        args: list[str],
        expected_returncode: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
        if completed.returncode != expected_returncode:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
