from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .config import configured_coder_command, load_config
from .controlled_git import ControlledGitClient
from .core import Actor, ActorKind, ProjectRuntime
from .core.result import ToolResult
from .kernel import Kernel, RunOptions
from .models import RunSummary, SpecError, TaskSpec
from .runlog import RunLogEntry, list_run_logs, load_run_log


@dataclass(frozen=True)
class APOSStatus:
    root: str
    version: str
    branch: str
    dirty: bool
    status_porcelain: str
    coder_command_configured: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "version": self.version,
            "branch": self.branch,
            "dirty": self.dirty,
            "status_porcelain": self.status_porcelain,
            "coder_command_configured": self.coder_command_configured,
        }


class APOSApplicationService:
    """Production-safe programmatic boundary for APOS development tasks."""

    def __init__(self, root: Path, actor: Actor | None = None) -> None:
        self.root = root.resolve()
        self.actor = actor or Actor(ActorKind.USER, "local-cli")

    def validate_task(self, path: str | Path) -> ToolResult[dict[str, Any]]:
        runtime = ProjectRuntime.create_read_only(self.root)
        return runtime.validation.validate(_service_path(self.root, path), actor=self.actor)

    def run_task(self, spec: TaskSpec, options: RunOptions | None = None) -> RunSummary:
        spec.validate()
        kernel = Kernel(
            self.root,
            test_runner_factory=self._local_test_runner,
            git_client_factory=self._local_git_client,
        )
        return kernel.run_task(spec, options or RunOptions())

    def run_task_file(self, path: str | Path, options: RunOptions | None = None) -> RunSummary:
        result = self.validate_task(path)
        if not result.success:
            assert result.error is not None
            raise SpecError(f"{result.error.code.value}: {result.error.message}")
        assert result.data is not None
        payload = result.data.get("taskspec")
        if not isinstance(payload, dict):
            raise SpecError("validated TaskSpec result did not include taskspec data")
        return self.run_task(TaskSpec.from_mapping(payload), options)

    def get_status(self) -> APOSStatus:
        git = self._local_git_client(self.root)
        root = git.ensure_repo().resolve()
        git = self._local_git_client(root)
        status = _project_status(git.status_porcelain())
        config = load_config(root)
        command = configured_coder_command(root)
        configured = bool(command or config.get("local_coder", {}).get("command"))
        return APOSStatus(
            root=str(root),
            version=__version__,
            branch=git.current_branch(),
            dirty=bool(status),
            status_porcelain=status,
            coder_command_configured=configured,
        )

    def list_runs(self, limit: int = 20) -> list[RunLogEntry]:
        root = self._repo_root()
        return list_run_logs(root, limit=limit)

    def get_run(self, run_path: str) -> dict[str, object]:
        root = self._repo_root()
        return load_run_log(root, run_path)

    def _local_test_runner(self, root: Path):
        runtime = ProjectRuntime.create_local_test_execution(root)
        return runtime.test_execution.bind(actor=self.actor, approved_by=self.actor)

    def _local_git_client(self, root: Path) -> ControlledGitClient:
        runtime = ProjectRuntime.create_local_git_phase_a(root)
        session = runtime.git_execution.bind(actor=self.actor, approved_by=self.actor)
        return ControlledGitClient(root, session)

    def _repo_root(self) -> Path:
        return self._local_git_client(self.root).ensure_repo().resolve()


def _project_status(status: str) -> str:
    lines = [line for line in status.splitlines() if not _is_internal_status_line(line)]
    return "\n".join(lines)


def _is_internal_status_line(line: str) -> bool:
    return line[3:].startswith(".apos/") if len(line) > 3 else False


def _service_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        return candidate.as_posix()
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(candidate)
