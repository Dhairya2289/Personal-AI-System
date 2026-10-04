"""
Tool Registry with Risk Levels and Permission Enforcement.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"          # Read-only, queries, calculations (auto-approved)
    MEDIUM = "medium"    # Writing notes, benign file creation (audit logged)
    HIGH = "high"        # Command execution, deletion, system modifications (requires confirmation)


class ToolParameter(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any | None = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False


class ToolResult(BaseModel):
    success: bool
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False


class BaseTool:
    """Base class for executable agent tools."""

    name: str = "base_tool"
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            risk_level=self.risk_level,
            requires_confirmation=self.requires_confirmation or (self.risk_level == RiskLevel.HIGH),
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    """Manages tool registration, permission checks, and execution."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    def check_permission(self, tool_name: str, user_confirmed: bool = False) -> tuple[bool, str]:
        """
        Enforce safety boundary:
        - LOW risk: Always allowed
        - MEDIUM risk: Allowed with audit
        - HIGH risk: Allowed ONLY if user_confirmed is True
        """
        tool = self.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' not found."

        if tool.risk_level == RiskLevel.HIGH and not user_confirmed:
            return (
                False,
                f"Confirmation required: Tool '{tool_name}' is classified as HIGH RISK ({tool.description}).",
            )

        return True, "Permission granted"

    async def execute(
        self, tool_name: str, kwargs: dict[str, Any], user_confirmed: bool = False
    ) -> ToolResult:
        """Safely execute tool after running permission checks."""
        allowed, reason = self.check_permission(tool_name, user_confirmed=user_confirmed)
        tool = self.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=reason, risk_level=RiskLevel.LOW)

        if not allowed:
            return ToolResult(
                success=False,
                error=reason,
                risk_level=tool.risk_level,
                requires_confirmation=True,
            )

        start = time.perf_counter()
        try:
            res = await tool.execute(**kwargs)
            res.duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
            res.risk_level = tool.risk_level
            return res
        except Exception as exc:
            duration = round((time.perf_counter() - start) * 1000.0, 2)
            return ToolResult(
                success=False,
                error=str(exc),
                duration_ms=duration,
                risk_level=tool.risk_level,
            )


_global_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry
