"""
Base agent data models and status representations.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.tools.registry import ToolResult


class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionStep(BaseModel):
    step_number: int
    thought: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: ToolResult | None = None
    requires_confirmation: bool = False


class AgentResponse(BaseModel):
    final_answer: str = ""
    steps: list[ActionStep] = Field(default_factory=list)
    status: AgentStatus = AgentStatus.COMPLETED
    pending_confirmation: dict[str, Any] | None = None
    error: str | None = None
