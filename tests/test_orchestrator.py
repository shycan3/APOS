import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from apos.evolution import EvolutionPolicy
from apos.orchestrator import (
    format_status,
    get_project_summary,
    guide_evolution,
    interactive_create_proposal_and_candidate,
    interactive_evaluate_candidate,
    interactive_review_candidate,
    interactive_run_candidate,
    interactive_show_status,
    print_dashboard,
    run_orchestrator,
)


class OrchestratorTests(unittest.TestCase):
    def test_format_status_translates_korean(self):
        self.assertEqual(format_status("ACTIVE"), "활성(ACTIVE)")
        self.assertEqual(format_status("PROMOTABLE"), "승격 가능(PROMOTABLE)")
        self.assertEqual(format_status("CUSTOM_STATUS"), "CUSTOM_STATUS")

    def test_get_project_summary_and_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)

            summary = get_project_summary(root)
            self.assertEqual(summary["root"], root)
            self.assertTrue(summary["is_clean"])
            self.assertTrue(summary["policy_valid"])

            lines: list[str] = []
            print_dashboard(summary, output_func=lines.append)
            dashboard_text = "\n".join(lines)
            self.assertIn("APOS", dashboard_text)
            self.assertIn("오케스트레이터", dashboard_text)
            self.assertIn("진화 기준선: v1.1.0", dashboard_text)
            self.assertIn("활성(ACTIVE)", dashboard_text)

    def test_run_orchestrator_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)

            lines: list[str] = []
            inputs = iter(["0"])
            exit_code = run_orchestrator(
                root=root,
                input_func=lambda prompt: next(inputs),
                output_func=lines.append,
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(any("종료합니다" in line for line in lines))

    def test_interactive_show_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)

            lines: list[str] = []
            interactive_show_status(root, output_func=lines.append)
            text = "\n".join(lines)
            self.assertIn("상세 진화 및 거버넌스 상태", text)
            self.assertIn("기준선: v1.1.0", text)
            self.assertIn("자동 승격: 비활성화", text)

    def test_interactive_create_proposal_and_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)

            lines: list[str] = []
            inputs = iter([
                "Add interactive orchestrator",  # goal
                "1.2.0",                         # target_version
                "test-cand-01",                  # candidate_id
                "app.py, pyproject.toml, src/apos/__init__.py",  # allowed_files
            ])
            interactive_create_proposal_and_candidate(
                root,
                input_func=lambda prompt: next(inputs),
                output_func=lines.append,
            )
            text = "\n".join(lines)
            self.assertIn("진화 후보 등록 완료: test-cand-01", text)
            self.assertTrue((root / ".apos" / "evolution" / "candidates" / "test-cand-01" / "candidate.json").exists())

    def test_guided_evolution_workflow(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as coder_tmp:
            root = Path(tmp)
            self._initialize_repo(root)

            # Create a fake coder
            coder = Path(coder_tmp) / "fake_coder.py"
            coder.write_text(
                """
import sys
sys.stdin.read()
print('''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
diff --git a/pyproject.toml b/pyproject.toml
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,2 +1,2 @@
 [project]
-version = "1.1.0"
+version = "1.2.0"
diff --git a/src/apos/__init__.py b/src/apos/__init__.py
--- a/src/apos/__init__.py
+++ b/src/apos/__init__.py
@@ -1 +1 @@
-__version__ = "1.1.0"
+__version__ = "1.2.0"
''', end='')
""".lstrip(),
                encoding="utf-8",
            )

            # Configure coder command
            config_file = root / ".apos" / "config.json"
            config = {"local_coder": {"command": f'"{sys.executable}" "{coder}"'}}
            config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
            self._run(root, ["git", "add", ".apos/config.json"])
            self._run(root, ["git", "commit", "-m", "config coder"])

            lines: list[str] = []
            inputs = iter([
                "Improve APOS interactive usability",  # goal
                "1.2.0",                               # target_version
                "guided-evo-1",                        # candidate_id
                "y",                                   # confirm candidate create
                "y",                                   # confirm run
                "y",                                   # confirm evaluate
                "y",                                   # codex approve
                "AI verification passed",              # codex note
                "y",                                   # human approve
                "Human approval for orchestrator",     # human note
            ])

            success = guide_evolution(
                root,
                input_func=lambda prompt: next(inputs),
                output_func=lines.append,
            )
            text = "\n".join(lines)
            self.assertTrue(success)
            self.assertIn("후보 생성 완료", text)
            self.assertIn("개발 결과: 통과(PASS)", text)
            self.assertIn("평가 결과: 검수 준비 완료(READY_FOR_REVIEW)", text)
            self.assertIn("Codex 검수 승인 기록 완료", text)
            self.assertIn("사용자(Human) 승인 기록 완료", text)
            self.assertIn("후보가 승격 가능(PROMOTABLE) 상태가 되었습니다", text)
            self.assertIn("git merge --no-ff apos/evolution/guided-evo-1", text)

    def test_interactive_review_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as coder_tmp:
            root = Path(tmp)
            self._initialize_repo(root)

            coder = Path(coder_tmp) / "fake_coder.py"
            coder.write_text(
                """
import sys
sys.stdin.read()
print('''diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
diff --git a/pyproject.toml b/pyproject.toml
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,2 +1,2 @@
 [project]
-version = "1.1.0"
+version = "1.2.0"
diff --git a/src/apos/__init__.py b/src/apos/__init__.py
--- a/src/apos/__init__.py
+++ b/src/apos/__init__.py
@@ -1 +1 @@
-__version__ = "1.1.0"
+__version__ = "1.2.0"
''', end='')
""".lstrip(),
                encoding="utf-8",
            )
            config_file = root / ".apos" / "config.json"
            config = {"local_coder": {"command": f'"{sys.executable}" "{coder}"'}}
            config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")
            self._run(root, ["git", "add", ".apos/config.json"])
            self._run(root, ["git", "commit", "-m", "config coder"])

            # Set up a candidate and evaluate it
            inputs_setup = iter([
                "Improve APOS", "1.2.0", "review-cand", "app.py, pyproject.toml, src/apos/__init__.py"
            ])
            interactive_create_proposal_and_candidate(root, input_func=lambda p: next(inputs_setup), output_func=lambda s: None)

            # Run & evaluate
            inputs_run = iter(["1"])
            interactive_run_candidate(root, input_func=lambda p: next(inputs_run), output_func=lambda s: None)
            inputs_eval = iter(["1", "n"])
            interactive_evaluate_candidate(root, input_func=lambda p: next(inputs_eval), output_func=lambda s: None)

            # Now test interactive review
            lines: list[str] = []
            inputs_review = iter([
                "1",          # select candidate 1
                "1",          # reviewer: codex
                "1",          # decision: approve
                "Looks good", # note
            ])
            interactive_review_candidate(root, input_func=lambda p: next(inputs_review), output_func=lines.append)
            text = "\n".join(lines)
            self.assertIn("검수 기록 완료", text)
            self.assertIn("검수 대기(AWAITING_REVIEWS)", text)

    @classmethod
    def _initialize_repo(cls, root: Path) -> None:
        cls._run(root, ["git", "init"])
        cls._run(root, ["git", "config", "user.email", "apos@example.test"])
        cls._run(root, ["git", "config", "user.name", "APOS Test"])
        (root / ".apos").mkdir()
        (root / "src" / "apos").mkdir(parents=True)
        (root / "benchmarks").mkdir()
        (root / "tasks").mkdir()
        (root / ".gitignore").write_text(".apos/evolution/\n.apos/runs/\n.apos/benchmarks/\n__pycache__/\n", encoding="utf-8")
        from apos.config import ensure_project_memory
        ensure_project_memory(root)
        cls._write_json(root / ".apos" / "evolution-policy.json", cls._policy_data())
        cls._write_versions(root, "1.1.0")
        (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "check.py").write_text(
            """
from pathlib import Path
from app import VALUE

assert VALUE == 2
assert 'version = "1.2.0"' in Path('pyproject.toml').read_text(encoding='utf-8')
assert '__version__ = "1.2.0"' in Path('src/apos/__init__.py').read_text(encoding='utf-8')
""".lstrip(),
            encoding="utf-8",
        )
        cls._write_json(
            root / "tasks" / "task-eval.json",
            {
                "task_id": "TASK-EVAL",
                "goal": "Verify the evolved candidate state.",
                "allowed_files": ["app.py"],
                "test_commands": ["python check.py"],
            },
        )
        cls._write_json(
            root / "benchmarks" / "suite.json",
            {
                "suite_id": "evolution-test-suite",
                "title": "Evolution Test Suite",
                "tasks": [{"task_id": "TASK-EVAL", "path": "tasks/task-eval.json"}],
            },
        )
        (root / "benchmark_stub.py").write_text(
            """
import json
import subprocess

branch = 'apos/benchmark/stub/task-eval'
subprocess.run(['git', 'branch', '-f', branch, 'HEAD'], check=True)
print(json.dumps({
    'padding': 'x' * 7000,
    'status': 'PASS',
    'result_id': 'stub-result',
    'result_path': '.apos/benchmarks/stub/result.json',
    'summary': {'total_tasks': 1, 'passed_tasks': 1, 'average_quality_score': 82.0},
    'tasks': [{
        'task': {'task_id': 'TASK-EVAL'},
        'status': 'PASS',
        'summary': {'branch': branch},
    }],
}))
""".lstrip(),
            encoding="utf-8",
        )
        cls._run(root, ["git", "add", "."])
        cls._run(root, ["git", "commit", "-m", "APOS 1.1 baseline"])
        cls._run(root, ["git", "tag", "v1.1.0"])

    @staticmethod
    def _policy_data() -> dict[str, object]:
        return {
            "schema_version": 1,
            "baseline": {"version": "1.1.0", "ref": "v1.1.0", "evidence": {"score": 70}},
            "candidates": {"branch_prefix": "apos/evolution/", "maximum_version_exclusive": "2.0.0"},
            "verification": {
                "required_test_commands": ["python check.py"],
                "benchmark": {
                    "command": "python benchmark_stub.py",
                    "suite": "benchmarks/suite.json",
                    "trusted_replay": True,
                    "minimum_pass_rate": 1.0,
                    "minimum_quality_score": 70.0,
                },
            },
            "governance": {
                "immutable_paths": [
                    ".apos/evolution-policy.json",
                    "benchmark_stub.py",
                    "benchmarks/suite.json",
                    "tasks/task-eval.json",
                    "check.py",
                ],
                "required_reviewers": ["codex", "human"],
                "auto_promotion": False,
            },
        }

    @staticmethod
    def _write_versions(root: Path, version: str) -> None:
        (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
        package = root / "src" / "apos"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        return completed


if __name__ == "__main__":
    unittest.main()
