"""
Unit tests for the Executor and Planner agents.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agents.base import AgentStatus
from app.agents.executor import ExecutorAgent
from app.agents.planner import PlannerAgent
from app.providers.base import LLMResponse, LLMUsage
from app.providers.manager import ProviderManager
from app.tools.filesystem import WriteFileTool
from app.tools.registry import ToolRegistry


def make_mock_resp(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        model="mock-model",
        provider="mock-provider",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
    )


@pytest.mark.asyncio
async def test_planner_agent_parses_json():
    mock_pm = AsyncMock(spec=ProviderManager)
    plan_json = """{
        "goal": "Build authentication",
        "rationale": "Security first",
        "tasks": [
            {"step_id": 1, "title": "Check config", "description": "Verify env vars", "suggested_tool": "read_file", "risk_level": "low"}
        ]
    }"""
    mock_pm.generate.return_value = make_mock_resp(plan_json)

    planner = PlannerAgent(provider_manager=mock_pm)
    plan = await planner.plan("Build authentication")

    assert plan.goal == "Build authentication"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].title == "Check config"


@pytest.mark.asyncio
async def test_executor_agent_react_loop():
    mock_pm = AsyncMock(spec=ProviderManager)
    # First turn: call write_file
    # Second turn: final answer
    mock_pm.generate.side_effect = [
        make_mock_resp('Thought: I should write a test file.\nAction: write_file\nAction Input: {"path": "/tmp/test_react.txt", "content": "agent text"}'),
        make_mock_resp("Thought: The file is written.\nFinal Answer: Task completed successfully."),
    ]

    reg = ToolRegistry()
    reg.register(WriteFileTool())

    executor = ExecutorAgent(provider_manager=mock_pm, tool_registry=reg)
    resp = await executor.run("Create a test file")

    assert resp.status == AgentStatus.COMPLETED
    assert "Task completed successfully" in resp.final_answer
    assert len(resp.steps) >= 1
    assert resp.steps[0].tool_name == "write_file"



@pytest.mark.asyncio
async def test_executor_high_risk_confirmation_round_trip(tmp_path):
    mock_pm = AsyncMock(spec=ProviderManager)
    command = f"echo safe"
    mock_pm.generate.side_effect = [
        make_mock_resp(
            "Thought: I need to run the command.\n"
            "Action: terminal_run\n"
            f'Action Input: {{"command": "{command}"}}'
        ),
        make_mock_resp("Thought: Done.\nFinal Answer: Command completed."),
    ]

    from app.tools.terminal import RunCommandTool

    reg = ToolRegistry()
    reg.register(RunCommandTool())

    executor = ExecutorAgent(provider_manager=mock_pm, tool_registry=reg)

    pending = await executor.run("Run a safe command")
    assert pending.status == AgentStatus.WAITING_CONFIRMATION
    assert pending.pending_confirmation is not None

    token = pending.pending_confirmation["confirmation_token"]
    assert token

    confirmed = await executor.run(
        "Run a safe command",
        history=pending.steps,
        confirmation_token=token,
    )
    assert confirmed.status == AgentStatus.COMPLETED
    assert "Command completed" in confirmed.final_answer
    assert confirmed.steps[-1].tool_name == "terminal_run"
    assert confirmed.steps[-1].tool_result is not None
    assert confirmed.steps[-1].tool_result.success
