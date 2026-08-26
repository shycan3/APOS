from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a Git command fails."""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "task"


@dataclass(frozen=True)
class GitClient:
    root: Path

    def run(self, args: list[str], input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            input=input_text,
            text=True,
            capture_output=True,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise GitError(f"git {' '.join(args)} failed: {detail}")
        return completed

    def ensure_repo(self) -> Path:
        completed = self.run(["rev-parse", "--show-toplevel"])
        return Path(completed.stdout.strip())

    def has_commits(self) -> bool:
        return self.run(["rev-parse", "--verify", "HEAD"], check=False).returncode == 0

    def current_branch(self) -> str:
        completed = self.run(["branch", "--show-current"], check=False)
        branch = completed.stdout.strip()
        return branch or "HEAD"

    def status_porcelain(self) -> str:
        return self.run(["status", "--porcelain"], check=False).stdout

    def changed_files(self) -> list[str]:
        tracked = self.run(["diff", "--name-only"], check=False).stdout.splitlines()
        staged = self.run(["diff", "--cached", "--name-only"], check=False).stdout.splitlines()
        untracked = self.run(["ls-files", "--others", "--exclude-standard"], check=False).stdout.splitlines()
        return sorted(set(tracked + staged + untracked))

    def exclude_path(self, pattern: str) -> None:
        exclude_path = self.run(["rev-parse", "--git-path", "info/exclude"]).stdout.strip()
        path = self.root / exclude_path
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if pattern in existing.splitlines():
            return
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        path.write_text(f"{existing}{prefix}# APOS runtime artifacts\n{pattern}\n", encoding="utf-8")

    def branch_name_for_task(self, task_id: str, title: str, prefix: str = "apos/task-") -> str:
        normalized_id = _slug(task_id)
        if normalized_id.startswith("task-"):
            normalized_id = normalized_id.removeprefix("task-")
        normalized_title = _slug(title)
        return f"{prefix}{normalized_id}-{normalized_title}"

    def checkout_task_branch(self, branch: str) -> None:
        if self.current_branch() == branch:
            return
        exists = self.run(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0
        if exists:
            self.run(["checkout", branch])
        else:
            self.run(["checkout", "-b", branch])

    def apply_patch(self, patch: str) -> None:
        self.run(["apply", "--recount", "--ignore-space-change", "--check", "-"], input_text=patch)
        self.run(["apply", "--recount", "--ignore-space-change", "-"], input_text=patch)

    def diff(self) -> str:
        return self.run(["diff"], check=False).stdout

    def commit(self, files: list[str], message: str) -> str:
        if not files:
            raise GitError("no files to commit")
        self.run(["add", "--", *files])
        if self.run(["diff", "--cached", "--quiet"], check=False).returncode == 0:
            raise GitError("no staged changes to commit")
        self.run(["commit", "-m", message])
        return self.run(["rev-parse", "--short", "HEAD"]).stdout.strip()
