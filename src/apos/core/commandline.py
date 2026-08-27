from __future__ import annotations

import os
import shlex


def parse_legacy_command(command: str) -> list[str]:
    """Parse the legacy string command contract without invoking a shell."""

    if not command.strip():
        raise ValueError("command must not be empty")
    arguments = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        arguments = [_strip_balanced_quotes(argument) for argument in arguments]
    if not arguments:
        raise ValueError("command must contain an executable")
    return arguments


def _strip_balanced_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
