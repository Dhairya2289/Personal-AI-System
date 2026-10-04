"""
Tool Registry with Risk Levels and Permission Enforcement.
"""
from __future__ import annotations

import hashlib
import json
import secrets
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
    """Manages tool registration, permission checks, confirmations, and execution."""

    CONFIRMATION_TTL_SECONDS = 120.0

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._pending_confirmations: dict[str, tuple[str, str, float]] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    @staticmethod
    def _args_fingerprint(tool_name: str, kwargs: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"tool": tool_name, "args": kwargs},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def issue_confirmation(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        *,
        ttl_seconds: float | None = None,
    ) -> tuple[str, float]:
        """Create a short-lived, single-use confirmation token bound to exact tool arguments."""
        tool = self.get(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found.")
        if tool.risk_level != RiskLevel.HIGH:
            raise ValueError(f"Tool '{tool_name}' does not require confirmation.")

        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.CONFIRMATION_TTL_SECONDS
        expires_at = now + max(1.0, float(ttl))
        token = secrets.token_urlsafe(24)
        self._pending_confirmations[token] = (
            tool_name,
            self._args_fingerprint(tool_name, kwargs),
            expires_at,
        )
        self._purge_expired_confirmations(now=now)
        return token, expires_at

    def _purge_expired_confirmations(self, *, now: float | None = None) -> None:
        current = now if now is not None else time.time()
        expired = [token for token, (_, _, expires_at) in self._pending_confirmations.items() if expires_at <= current]
        for token in expired:
            self._pending_confirmations.pop(token, None)

    def _consume_confirmation(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        confirmation_token: str | None,
    ) -> bool:
        if not confirmation_token:
            return False

        self._purge_expired_confirmations()
        pending = self._pending_confirmations.get(confirmation_token)
        if not pending:
            return False

        expected_tool, expected_fingerprint, _ = pending
        if expected_tool != tool_name:
            return False
        if expected_fingerprint != self._args_fingerprint(tool_name, kwargs):
            return False

        # Single-use: consume before execution so a token cannot be replayed.
        self._pending_confirmations.pop(confirmation_token, None)
        return True

    def check_permission(
        self,
        tool_name: str,
        *,
        confirmation_token: str | None = None,
    ) -> tuple[bool, str]:
        """
        Enforce the safety boundary:
        - LOW risk: always allowed
        - MEDIUM risk: allowed with audit at the caller
        - HIGH risk: requires a valid confirmation token bound to the exact action
        """
        tool = self.get(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' not found."

        if tool.risk_level == RiskLevel.HIGH and not confirmation_token:
            return (
                False,
                f"Confirmation required: Tool '{tool_name}' is classified as HIGH RISK ({tool.description}).",
            )

        if tool.risk_level == RiskLevel.HIGH:
            return True, "Confirmation token supplied; action still must be validated against exact arguments."

        return True, "Permission granted"

    async def execute(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        *,
        confirmation_token: str | None = None,
    ) -> ToolResult:
        """Safely execute a tool after validating its risk policy and confirmation token."""
        tool = self.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found.", risk_level=RiskLevel.LOW)

        if tool.risk_level == RiskLevel.HIGH:
            if not self._consume_confirmation(tool_name, kwargs, confirmation_token):
                return ToolResult(
                    success=False,
                    error=f"Valid confirmation token required for HIGH RISK tool '{tool_name}'.",
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
