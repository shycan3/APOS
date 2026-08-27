from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .permissions import Capability, RiskLevel


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    capability: Capability
    risk_level: RiskLevel
    project_scoped: bool = True
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "capability": self.capability.value,
            "risk_level": self.risk_level.name,
            "project_scoped": self.project_scoped,
            "input_schema": dict(self.input_schema),
        }


class ToolRegistry:
    """Provider-neutral metadata registry; execution remains in capability services."""

    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool is already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"unknown APOS tool: {name}") from exc

    def list(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions[name] for name in sorted(self._definitions))


def core_tool_registry() -> ToolRegistry:
    path_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    return ToolRegistry(
        (
            ToolDefinition(
                "filesystem.list", "List non-secret paths inside the active project.",
                Capability.PROJECT_READ, RiskLevel.LOW,
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}},
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                "filesystem.read", "Read one non-secret file inside the active project.",
                Capability.PROJECT_READ, RiskLevel.LOW, input_schema=path_schema,
            ),
            ToolDefinition(
                "filesystem.write", "Atomically write one non-secret project file.",
                Capability.PROJECT_WRITE, RiskLevel.MEDIUM,
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                "execution.run", "Run an explicitly trusted executable without a shell.",
                Capability.PROCESS_EXECUTE, RiskLevel.HIGH,
                input_schema={
                    "type": "object",
                    "properties": {
                        "executable": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cwd": {"type": "string"},
                    },
                    "required": ["executable"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                "test.run", "Run an explicitly authorized project test command.",
                Capability.TEST_EXECUTE, RiskLevel.MEDIUM,
                input_schema={
                    "type": "object",
                    "properties": {
                        "executable": {"type": "string"},
                        "args": {"type": "array", "items": {"type": "string"}},
                        "cwd": {"type": "string"},
                    },
                    "required": ["executable"],
                    "additionalProperties": False,
                },
            ),
        )
    )
