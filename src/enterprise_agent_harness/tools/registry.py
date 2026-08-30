"""Explicit registry for versioned tools."""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts import ToolDescriptor
from .definitions import ToolDefinition, ToolInvocationError


class ToolRegistry:
    """Resolve only tools that the application registered."""

    def __init__(self, tools: Iterable[ToolDefinition] = ()) -> None:
        values = list(tools)
        keys = [(tool.tool_id, tool.version) for tool in values]
        if len(keys) != len(set(keys)):
            raise ValueError("tool identity and version pairs must be unique")
        self._tools = {(tool.tool_id, tool.version): tool for tool in values}

    def get(self, tool_id: str, version: str | None = None) -> ToolDefinition:
        """Resolve a tool by identity and, when needed, exact version."""

        matches = [
            tool for (candidate_id, _), tool in self._tools.items() if candidate_id == tool_id
        ]
        if version is not None:
            try:
                return self._tools[(tool_id, version)]
            except KeyError as exc:
                raise ToolInvocationError(
                    f"unknown tool version: {tool_id}@{version}",
                    code="unknown_tool",
                ) from exc
        if not matches:
            raise ToolInvocationError(f"unknown tool: {tool_id}", code="unknown_tool")
        if len(matches) > 1:
            raise ToolInvocationError(
                f"tool version is required: {tool_id}",
                code="tool_version_required",
            )
        return matches[0]

    def descriptors(self, allowed_tool_ids: Iterable[str] | None = None) -> list[ToolDescriptor]:
        """Return stable provider-facing descriptors.

        The returned objects contain no handlers and no authority grants.
        """

        allowed = set(allowed_tool_ids) if allowed_tool_ids is not None else None
        values = [
            tool.descriptor
            for tool in self._tools.values()
            if allowed is None or tool.tool_id in allowed
        ]
        return sorted(values, key=lambda item: (item.tool_id, item.version))

    def names(self) -> tuple[str, ...]:
        """Return unique registered tool identities in stable order."""

        return tuple(sorted({tool.tool_id for tool in self._tools.values()}))

    def versions(self, tool_id: str) -> tuple[str, ...]:
        """Return registered versions for one identity."""

        return tuple(sorted(version for candidate, version in self._tools if candidate == tool_id))
