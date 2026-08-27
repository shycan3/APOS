from __future__ import annotations

from dataclasses import dataclass
import subprocess
from pathlib import Path

from .core.git_execution import GitExecutionError, GitExecutionSession
from .git import GitClient, GitError


@dataclass(frozen=True)
class ControlledGitClient:
    """Production Git adapter that migrates Phase A operations to the core control plane."""

    root: Path
    session: GitExecutionSession

    def __post_init__(self) -> None:
        object.__setattr__(self, "_legacy", GitClient(self.root))

    def ensure_repo(self) -> Path:
        return self._translate(self.session.ensure_repo)

    def has_commits(self) -> bool:
        return self._translate(self.session.has_commits)

    def current_branch(self) -> str:
        return self._translate(self.session.current_branch)

    def status_porcelain(self) -> str:
        return self._translate(self.session.status_porcelain)

    def changed_files(self) -> list[str]:
        return self._legacy.changed_files()

    def exclude_path(self, pattern: str) -> None:
        self._legacy.exclude_path(pattern)

    def branch_name_for_task(self, task_id: str, title: str, prefix: str = "apos/task-") -> str:
        return self.session.branch_name_for_task(task_id, title, prefix=prefix)

    def checkout_task_branch(self, branch: str) -> None:
        self._translate(lambda: self.session.checkout_task_branch(branch))

    def apply_patch(self, patch: str) -> None:
        self._legacy.apply_patch(patch)

    def reverse_patch(self, patch: str) -> None:
        self._legacy.reverse_patch(patch)

    def diff(self) -> str:
        return self._legacy.diff()

    def commit(self, files: list[str], message: str) -> str:
        return self._legacy.commit(files, message)

    def run(
        self,
        args: list[str],
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self._legacy.run(args, input_text=input_text, check=check)

    @staticmethod
    def _translate(action):
        try:
            return action()
        except GitExecutionError as exc:
            raise GitError(str(exc)) from exc
