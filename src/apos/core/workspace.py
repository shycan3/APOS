from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePosixPath

from .result import ErrorCode


class WorkspaceViolation(ValueError):
    def __init__(self, code: ErrorCode, message: str, *, path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class SecretPolicy:
    """Conservative default policy for paths that must not reach an AI caller."""

    denied_directories: frozenset[str] = field(
        default_factory=lambda: frozenset({".git", ".apos", ".ssh", ".gnupg"})
    )
    denied_names: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                ".env",
                "credentials.json",
                "secrets.json",
                "id_rsa",
                "id_ed25519",
            }
        )
    )
    denied_suffixes: tuple[str, ...] = (".key", ".pem", ".p12", ".pfx")

    def denial_reason(self, relative_path: str) -> str | None:
        parts = PurePosixPath(relative_path).parts
        lowered = tuple(part.casefold() for part in parts)
        denied_directories = {name.casefold() for name in self.denied_directories}
        if any(part in denied_directories for part in lowered):
            return "path is inside an APOS-internal or credential directory"

        name = lowered[-1] if lowered else ""
        if name in {item.casefold() for item in self.denied_names} or name.startswith(".env."):
            return "path name is classified as secret"
        if any(name.endswith(suffix.casefold()) for suffix in self.denied_suffixes):
            return "path extension is classified as secret"
        return None


def normalize_relative_path(path: str, *, allow_root: bool = False) -> str:
    raw = str(path).strip().replace("\\", "/")
    if allow_root and raw in {"", "."}:
        return ""
    if not raw:
        raise WorkspaceViolation(ErrorCode.INVALID_ARGUMENT, "path must not be empty", path=path)
    if Path(raw).is_absolute() or raw.startswith("/"):
        raise WorkspaceViolation(
            ErrorCode.PATH_OUTSIDE_PROJECT,
            "absolute paths are outside the project capability",
            path=path,
        )

    normalized = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in normalized.parts):
        raise WorkspaceViolation(
            ErrorCode.PATH_OUTSIDE_PROJECT,
            "path traversal is outside the project capability",
            path=path,
        )
    return normalized.as_posix()


@dataclass(frozen=True)
class ProjectWorkspace:
    root: Path
    project_id: str
    secret_policy: SecretPolicy = field(default_factory=SecretPolicy)

    @classmethod
    def register(cls, root: Path, *, secret_policy: SecretPolicy | None = None) -> "ProjectWorkspace":
        try:
            canonical_root = root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise WorkspaceViolation(ErrorCode.PATH_NOT_FOUND, f"project root does not exist: {root}") from exc
        if not canonical_root.is_dir():
            raise WorkspaceViolation(ErrorCode.PATH_NOT_DIRECTORY, f"project root is not a directory: {root}")

        identity = os.path.normcase(str(canonical_root)).encode("utf-8")
        project_id = hashlib.sha256(identity).hexdigest()[:16]
        return cls(canonical_root, project_id, secret_policy or SecretPolicy())

    def resolve(
        self,
        relative_path: str,
        *,
        allow_root: bool = False,
        must_exist: bool = False,
        allow_secret: bool = False,
    ) -> tuple[str, Path]:
        normalized = normalize_relative_path(relative_path, allow_root=allow_root)
        if normalized and not allow_secret:
            reason = self.secret_policy.denial_reason(normalized)
            if reason:
                raise WorkspaceViolation(ErrorCode.SECRET_PATH_DENIED, reason, path=normalized)

        candidate = (self.root / normalized).resolve(strict=False)
        try:
            canonical_relative = candidate.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise WorkspaceViolation(
                ErrorCode.PATH_OUTSIDE_PROJECT,
                "resolved path is outside the project root",
                path=normalized,
            ) from exc

        if canonical_relative and not allow_secret:
            reason = self.secret_policy.denial_reason(canonical_relative)
            if reason:
                raise WorkspaceViolation(ErrorCode.SECRET_PATH_DENIED, reason, path=normalized)

        if must_exist and not candidate.exists():
            raise WorkspaceViolation(ErrorCode.PATH_NOT_FOUND, "project path does not exist", path=normalized)
        return normalized, candidate

    def result_meta(self) -> dict[str, str]:
        return {"project_id": self.project_id}
