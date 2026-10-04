"""
Planner agent for decomposing goals into structured execution plans.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.providers.base import LLMMessage
from app.providers.manager import ProviderManager, get_provider_manager


class TaskItem(BaseModel):
    step_id: int
    title: str
    description: str
    suggested_tool: str | None = None
    risk_level: str = "low"


class ExecutionPlan(BaseModel):
    goal: str
    rationale: str
    tasks: list[TaskItem] = Field(default_factory=list)


PLANNER_SYSTEM_PROMPT = """You are the Lead Strategic Planner for Personal AI Mission Control.
Given a user's goal, break it down into an ordered series of clear, minimal, actionable steps.
For each step, specify:
1. `step_id`: sequential integer (1, 2, 3...)
2. `title`: brief task title
3. `description`: what needs to be accomplished
4. `suggested_tool`: name of tool if relevant (e.g. read_file, write_file, terminal_run, memory_search, memory_record, list_dir)
5. `risk_level`: 'low', 'medium', or 'high'

Return your response ONLY as valid JSON in this schema:
{
  "goal": "...",
  "rationale": "...",
  "tasks": [
    {"step_id": 1, "title": "...", "description": "...", "suggested_tool": "...", "risk_level": "low"}
  ]
}
"""


class PlannerAgent:
    """Creates structured multi-step execution plans."""

    def __init__(self, provider_manager: ProviderManager | None = None):
        self.provider_manager = provider_manager or get_provider_manager()

    async def plan(self, goal: str, model: str | None = None) -> ExecutionPlan:
        messages = [
            LLMMessage(role="system", content=PLANNER_SYSTEM_PROMPT),
            LLMMessage(role="user", content=f"Goal: {goal}"),
        ]

        resp = await self.provider_manager.generate(
            messages,
            task_type="logic",
            model=model,
            temperature=0.2,
        )

        text = resp.text.strip()
        # Clean markdown code blocks if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            data = json.loads(text)
            return ExecutionPlan(**data)
        except Exception:
            # Fallback if raw text returned
            return ExecutionPlan(
                goal=goal,
                rationale=resp.text[:200],
                tasks=[TaskItem(step_id=1, title="Execute Goal", description=goal)],
            )
