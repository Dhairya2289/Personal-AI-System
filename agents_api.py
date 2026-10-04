"""HTTP API for the modular Planner/Executor agent stack.

This router exposes the newer agent architecture without removing the
legacy Hermes subprocess endpoint used by the existing dashboard.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.agents import ActionStep, ExecutorAgent, PlannerAgent

router = APIRouter(prefix="/api/agents", tags=["agents"])


class PlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    model: str | None = None


class ExecuteRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    model: str | None = None
    confirmation_token: str | None = None
    history: list[ActionStep] = Field(default_factory=list)


@router.post("/plan")
async def plan_agent(payload: PlanRequest) -> dict[str, Any]:
    """Create a structured plan with the modular PlannerAgent."""
    result = await PlannerAgent().plan(payload.goal, model=payload.model)
    return jsonable_encoder(result)


@router.post("/execute")
async def execute_agent(payload: ExecuteRequest) -> dict[str, Any]:
    """Run the modular ExecutorAgent and support confirmation/resume flows."""
    result = await ExecutorAgent().run(
        payload.task,
        history=payload.history,
        confirmation_token=payload.confirmation_token,
        model=payload.model,
    )
    return jsonable_encoder(result)
