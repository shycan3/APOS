import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from apos.evolution import (
    EvolutionError,
    EvolutionPolicy,
    EvolutionProposal,
    candidate_promotion_status,
    create_candidate,
    evaluate_candidate,
    evolution_status,
    record_review,
    run_candidate,
    validate_evolution,
)
from apos.kernel import RunOptions


class EvolutionTests(unittest.TestCase):
    def test_rejects_unsafe_policy_and_proposal(self):
        policy_data = self._policy_data()
        policy_data["governance"]["auto_promotion"] = True
        with self.assertRaisesRegex(EvolutionError, "auto_promotion"):
            EvolutionPolicy.from_mapping(policy_data)

        policy_data["governance"]["auto_promotion"] = False
        policy = EvolutionPolicy.from_mapping(policy_data)
        proposal = self._proposal_data()
        proposal["allowed_files"].append(".apos/evolution-policy.json")
        with self.assertRaisesRegex(EvolutionError, "immutable"):
            EvolutionProposal.from_mapping(proposal, policy)

        proposal = self._proposal_data()
        proposal["target_version"] = "2.0.0"
        with self.assertRaisesRegex(EvolutionError, "lower than 2.0.0"):
            EvolutionProposal.from_mapping(proposal, policy)

    def test_creates_develops_evaluates_and_reviews_candidate(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as coder_tmp:
            root = Path(tmp)
            self._initialize_repo(root)
            proposal_path = root / "proposal.json"
            self._write_json(proposal_path, self._proposal_data())
            self._run(root, ["git", "add", "proposal.json"])
            self._run(root, ["git", "commit", "-m", "add proposal"])

            validation = validate_evolution(root)
            self.assertEqual(validation["status"], "VALID")
            self.assertEqual(validation["baseline_version"], "1.1.0")

            candidate = create_candidate(root, proposal_path, candidate_id="candidate-1")
            workspace = root / str(candidate["workspace"])
            self.assertTrue(workspace.exists())
            self.assertEqual(candidate["parent_ref"], "v1.1.0")
            self.assertEqual(candidate["target_version"], "1.2.0")

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
            development = run_candidate(
                root,
                "candidate-1",
                RunOptions(coder_command=f'"{sys.executable}" "{coder}"', command_timeout_seconds=30),
            )
            self.assertEqual(development["run"]["status"], "PASS", development)
            self.assertTrue(development["run"]["committed"])

            quick = evaluate_candidate(root, "candidate-1", quick=True, timeout_seconds=30)
            self.assertEqual(quick["status"], "INCOMPLETE", quick)
            with self.assertRaisesRegex(EvolutionError, "full passing evaluation"):
                record_review(root, "candidate-1", "codex", "approve", "Quick checks looked good.")

            report = evaluate_candidate(root, "candidate-1", timeout_seconds=30)
            self.assertEqual(report["status"], "READY_FOR_REVIEW", report)
            self.assertEqual(report["candidate_version"], "1.2.0")
            self.assertEqual(report["benchmark"]["pass_rate"], 1.0)
            self.assertTrue((root / str(report["review_path"])).exists())

            codex_review = record_review(root, "candidate-1", "codex", "approve", "Diff and evidence reviewed.")
            self.assertEqual(codex_review["promotion"]["status"], "AWAITING_REVIEWS")
            human_review = record_review(root, "candidate-1", "human", "approve", "Approved for manual promotion.")
            self.assertEqual(human_review["promotion"]["status"], "PROMOTABLE")
            self.assertFalse(human_review["promotion"]["automatic_promotion"])

            status = evolution_status(root, "candidate-1")
            self.assertEqual(status["promotion"]["status"], "PROMOTABLE")
            self.assertEqual(candidate_promotion_status(root, "candidate-1")["approvals"], ["codex", "human"])

            app_path = workspace / "app.py"
            app_path.write_text("VALUE = 3\n", encoding="utf-8")
            self.assertEqual(candidate_promotion_status(root, "candidate-1")["status"], "STALE_EVALUATION")
            app_path.write_text("VALUE = 2\n", encoding="utf-8")

    def test_rejects_policy_drift_from_baseline_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)
            policy_path = root / ".apos" / "evolution-policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["verification"]["benchmark"]["minimum_quality_score"] = 1.0
            self._write_json(policy_path, policy)
            with self.assertRaisesRegex(EvolutionError, "differs from the v1.1.0 policy"):
                validate_evolution(root)

    def test_rejects_candidate_that_changes_immutable_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)
            proposal_path = root / "proposal.json"
            self._write_json(proposal_path, self._proposal_data())
            self._run(root, ["git", "add", "proposal.json"])
            self._run(root, ["git", "commit", "-m", "add proposal"])
            candidate = create_candidate(root, proposal_path, candidate_id="candidate-unsafe")
            workspace = root / str(candidate["workspace"])
            policy_path = workspace / ".apos" / "evolution-policy.json"
            policy_path.write_text(policy_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self._write_versions(workspace, "1.2.0")
            (workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            self._run(workspace, ["git", "add", "."])
            self._run(workspace, ["git", "commit", "-m", "unsafe candidate"])

            report = evaluate_candidate(root, "candidate-unsafe", quick=True, timeout_seconds=30)
            self.assertEqual(report["status"], "REJECTED")
            immutable_gate = next(gate for gate in report["gates"] if gate["name"] == "immutable_controls")
            self.assertEqual(immutable_gate["status"], "FAIL")

    def test_cli_reports_evolution_status_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._initialize_repo(root)
            completed = self._run(root, [sys.executable, "-m", "apos", "evolution", "status", "--json"])
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ACTIVE")
            self.assertEqual(payload["baseline"]["version"], "1.1.0")
            self.assertFalse(payload["governance"]["auto_promotion"])

    @classmethod
    def _initialize_repo(cls, root: Path) -> None:
        cls._run(root, ["git", "init"])
        cls._run(root, ["git", "config", "user.email", "apos@example.test"])
        cls._run(root, ["git", "config", "user.name", "APOS Test"])
        (root / ".apos").mkdir()
        (root / "src" / "apos").mkdir(parents=True)
        (root / "benchmarks").mkdir()
        (root / "tasks").mkdir()
        (root / ".gitignore").write_text(".apos/evolution/\n__pycache__/\n", encoding="utf-8")
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
    def _proposal_data() -> dict[str, object]:
        return {
            "proposal_id": "EVOL-TEST",
            "title": "Improve test candidate",
            "goal": "Update the candidate value and version.",
            "target_version": "1.2.0",
            "risk": "medium",
            "allowed_files": ["app.py", "pyproject.toml", "src/apos/__init__.py"],
            "read_only_files": ["check.py"],
            "constraints": ["Keep the check script unchanged."],
            "expected_behavior": ["The candidate check passes."],
            "test_commands": ["python check.py"],
            "max_attempts": 2,
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
