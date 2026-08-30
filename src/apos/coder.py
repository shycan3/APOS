from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from .core.commandline import parse_legacy_command
from .models import ContextRequest, ExecutionResult, TaskSpec
from .pathing import project_path


class CoderError(RuntimeError):
    """Raised when a Local Coder cannot produce a usable patch."""


@dataclass(frozen=True)
class CoderResponse:
    type: str
    patch: str = ""
    message: str = ""
    request: ContextRequest | None = None
    path: str = ""
    content: str = ""


@dataclass(frozen=True)
class CommandPatchCoder:
    command: str | Sequence[str]
    timeout_seconds: int

    def run(self, prompt: str) -> CoderResponse:
        try:
            with tempfile.TemporaryDirectory(prefix="apos-coder-") as cwd:
                completed = subprocess.run(
                    parse_legacy_command(self.command) if isinstance(self.command, str) else self.command,
                    cwd=cwd,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    shell=False,
                    timeout=self.timeout_seconds,
                )
        except subprocess.TimeoutExpired:
            return CoderResponse(type="failed", message=f"coder command timed out after {self.timeout_seconds}s")
        except OSError as exc:
            return CoderResponse(type="failed", message=f"coder command could not start: {exc}")
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            return CoderResponse(type="failed", message=f"coder command failed: {detail}")

        output = completed.stdout
        if not output.strip():
            return CoderResponse(type="failed", message="coder command produced no output")
        return parse_coder_output(output)


def parse_coder_output(output: str) -> CoderResponse:
    stripped = output.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CoderError(f"invalid JSON coder output: {exc}") from exc
        if payload.get("type") == "request_permission":
            request = ContextRequest(
                type="read_file",
                path=str(payload.get("path", "")),
                permission=str(payload.get("permission", "read")),
                reason=str(payload.get("reason", "")),
            )
            return CoderResponse(type="request_permission", request=request)
        if payload.get("type") == "patch":
            patch = payload.get("patch")
            if not isinstance(patch, str) or not patch.strip():
                raise CoderError("JSON patch output must include a non-empty patch string")
            return CoderResponse(type="patch", patch=patch)
        if payload.get("type") == "file_replacement":
            path = payload.get("path")
            content = payload.get("content")
            if not isinstance(path, str) or not path.strip():
                raise CoderError("JSON file_replacement output must include a non-empty path string")
            if not isinstance(content, str):
                raise CoderError("JSON file_replacement output must include a content string")
            return CoderResponse(type="file_replacement", path=path, content=content)
        raise CoderError(f"unsupported JSON coder output type: {payload.get('type')}")
    return CoderResponse(type="patch", patch=output)


def build_coder_prompt(root: Path, spec: TaskSpec, attempt: int, previous_error: str | None = None) -> str:
    files = _collect_file_context(root, spec)
    payload = {
        "protocol": "APOS_LOCAL_CODER_PATCH_V1",
        "instructions": [
            "Return only a unified diff patch, a JSON file_replacement object, or a JSON request_permission object.",
            "Use file_replacement when a previous unified diff failed to apply or the edit is easier to express as a complete file.",
            "A file_replacement object must be {\"type\":\"file_replacement\",\"path\":\"path/to/file\",\"content\":\"complete final file text\"}.",
            "Modify only files listed in allowed_files.",
            "PATH AND WRITE AUTHORITY CONTRACT:",
            "1. allowed_files contains repository-relative filesystem paths that are already approved for modification.",
            "2. Modify allowed files directly; do not request write permission for a file listed in allowed_files.",
            "3. Use allowed file paths exactly as listed in patches, file_replacement objects, and permission requests.",
            "4. Do not remove, shorten, infer, or rewrite path prefixes.",
            "5. Python module names, import paths, and traceback module names are not repository filesystem paths.",
            "6. Never convert a Python module/import name into a filesystem path unless that exact path appears in allowed_files.",
            "7. Request permission only for a genuinely required repository-relative filesystem path outside allowed_files.",
            "Preserve the existing public API unless the TaskSpec explicitly allows a change.",
            "Do not include prose, markdown fences, or explanations around the patch.",
            "Every removed line in a diff hunk must be copied exactly from the files object.",
            "When replacing a line, include the exact old line prefixed with '-' and the new line prefixed with '+'.",
            "Do not use desired final code as unchanged context unless it already exists in the files object.",
        ],
        "attempt": attempt,
        "task": spec.to_dict(),
        "files": files,
        "previous_error": previous_error,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def summarize_failures(results: list[ExecutionResult]) -> str:
    if not results:
        return "No test results were produced."
    chunks: list[str] = []
    for result in results:
        if result.passed:
            continue
        chunks.append(
            "\n".join(
                [
                    f"Command: {result.command}",
                    f"Exit code: {result.exit_code}",
                    f"Summary: {result.summary}",
                    f"STDOUT:\n{_tail(result.stdout)}",
                    f"STDERR:\n{_tail(result.stderr)}",
                ]
            )
        )
    return "\n\n".join(chunks) or "All commands passed."


def _collect_file_context(root: Path, spec: TaskSpec) -> dict[str, str]:
    paths = list(dict.fromkeys(spec.read_only_files + spec.allowed_files))
    context: dict[str, str] = {}
    for relative in paths:
        path = project_path(root, relative)
        if path.exists():
            context[relative] = path.read_text(encoding="utf-8", errors="replace")
        else:
            context[relative] = "[APOS: file does not exist yet]"
    return context


def _tail(value: str, max_chars: int = 4000) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]
