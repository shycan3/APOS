from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.0",
    "local_coder": {"command": None},
    "ollama": {"model": None, "binary": "ollama", "host": "http://127.0.0.1:11434"},
    "defaults": {
        "branch_prefix": "apos/task-",
        "max_attempts": 3,
        "command_timeout_seconds": 120,
    },
}


PROJECT_MEMORY_FILES = {
    "README.md": "# APOS Memory\n\nCurrent project memory for APOS.\n",
    "current.md": "# Current State\n\n- APOS project memory initialized.\n",
    "decisions.md": "# Decisions\n\n",
    "warnings.md": "# Warnings\n\n",
    "ideas.md": "# Ideas\n\n",
    "tasks.md": "# Tasks\n\n",
}


class ConfigError(RuntimeError):
    """Raised when APOS configuration cannot be read or written."""


def apos_dir(root: Path) -> Path:
    return root / ".apos"


def config_path(root: Path) -> Path:
    return apos_dir(root) / "config.json"


def ensure_project_memory(root: Path) -> list[Path]:
    created: list[Path] = []
    directory = apos_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tasks").mkdir(exist_ok=True)

    cfg_path = config_path(root)
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        created.append(cfg_path)

    for name, content in PROJECT_MEMORY_FILES.items():
        path = directory / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
            created.append(path)
    return created


def load_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid APOS config at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("APOS config must be a JSON object")
    return merge_defaults(data)


def save_config(root: Path, config: dict[str, Any]) -> None:
    directory = apos_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    config_path(root).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def merge_defaults(config: dict[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def configured_coder_command(root: Path) -> str | None:
    env_command = os.environ.get("APOS_CODER_COMMAND")
    if env_command:
        return env_command
    config = load_config(root)
    command = config.get("local_coder", {}).get("command")
    return command if isinstance(command, str) and command.strip() else None


def configured_ollama(root: Path) -> tuple[str | None, str, str]:
    config = load_config(root)
    ollama = config.get("ollama", {})
    if not isinstance(ollama, dict):
        return None, "ollama", "http://127.0.0.1:11434"
    model = ollama.get("model")
    binary = ollama.get("binary") or "ollama"
    host = ollama.get("host") or "http://127.0.0.1:11434"
    return model if isinstance(model, str) and model.strip() else None, str(binary), str(host)
