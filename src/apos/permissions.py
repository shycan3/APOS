from __future__ import annotations

import re
from dataclasses import dataclass

from .models import PermissionSpec
from .pathing import PathPolicyError, normalize_project_path


class PermissionError(RuntimeError):
    """Raised when a coder attempts an unauthorized action."""


_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_FILE_HEADER_RE = re.compile(r"^(?:---|\+\+\+) (.+)$")


@dataclass(frozen=True)
class PermissionManager:
    spec: PermissionSpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "read", frozenset(_normalize_many(self.spec.read)))
        object.__setattr__(self, "write", frozenset(_normalize_many(self.spec.write)))

    def validate_patch(self, patch: str) -> list[str]:
        paths = sorted(_extract_patch_paths(patch))
        if not paths:
            raise PermissionError("coder output did not contain any changed file paths")
        self.validate_write_paths(paths)
        return paths

    def validate_write_paths(self, paths: list[str]) -> None:
        unauthorized = [path for path in paths if normalize_project_path(path) not in self.write]
        if unauthorized:
            formatted = ", ".join(sorted(unauthorized))
            allowed = ", ".join(sorted(self.write)) or "<none>"
            raise PermissionError(f"unauthorized write path(s): {formatted}; allowed: {allowed}")


def _normalize_many(paths: list[str]) -> list[str]:
    try:
        return [normalize_project_path(path) for path in paths]
    except PathPolicyError as exc:
        raise PermissionError(str(exc)) from exc


def _extract_patch_paths(patch: str) -> set[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        diff_match = _DIFF_GIT_RE.match(line)
        if diff_match:
            paths.add(normalize_project_path(diff_match.group(1)))
            paths.add(normalize_project_path(diff_match.group(2)))
            continue

        header_match = _FILE_HEADER_RE.match(line)
        if not header_match:
            continue
        path = header_match.group(1).strip()
        if path == "/dev/null":
            continue
        if path.startswith("a/") or path.startswith("b/"):
            paths.add(normalize_project_path(path[2:]))
    return paths

