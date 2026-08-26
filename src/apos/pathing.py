from __future__ import annotations

from pathlib import Path, PurePosixPath


class PathPolicyError(ValueError):
    """Raised when a path escapes the project policy."""


def normalize_project_path(path: str) -> str:
    raw = path.strip().replace("\\", "/")
    if not raw:
        raise PathPolicyError("empty paths are not allowed")
    if Path(raw).is_absolute() or raw.startswith("/"):
        raise PathPolicyError(f"absolute paths are not allowed: {path}")

    normalized = PurePosixPath(raw)
    if any(part in ("", ".", "..") for part in normalized.parts):
        raise PathPolicyError(f"path traversal is not allowed: {path}")
    return normalized.as_posix()


def project_path(root: Path, relative_path: str) -> Path:
    normalized = normalize_project_path(relative_path)
    candidate = (root / normalized).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PathPolicyError(f"path escapes project root: {relative_path}")
    return candidate

