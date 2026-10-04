import pytest

import agents_api


@pytest.mark.asyncio
async def test_plan_endpoint_uses_modular_planner(monkeypatch):
    calls = {}

    class FakePlanner:
        async def plan(self, goal, model=None):
            calls["goal"] = goal
            calls["model"] = model
            return {"goal": goal, "tasks": []}

    monkeypatch.setattr(agents_api, "PlannerAgent", FakePlanner)

    result = await agents_api.plan_agent(
        agents_api.PlanRequest(goal="organize my study plan", model="test-model")
    )

    assert result["goal"] == "organize my study plan"
    assert calls == {"goal": "organize my study plan", "model": "test-model"}


@pytest.mark.asyncio
async def test_execute_endpoint_preserves_confirmation_resume_data(monkeypatch):
    calls = {}

    class FakeExecutor:
        async def run(self, task, *, history, confirmation_token, model):
            calls.update(
                task=task,
                history=history,
                confirmation_token=confirmation_token,
                model=model,
            )
            return {
                "status": "waiting_confirmation",
                "pending_confirmation": {"tool_name": "delete_file"},
            }

    monkeypatch.setattr(agents_api, "ExecutorAgent", FakeExecutor)

    request = agents_api.ExecuteRequest(
        task="delete the temporary report",
        model="test-model",
        confirmation_token="token-123",
    )
    result = await agents_api.execute_agent(request)

    assert result["status"] == "waiting_confirmation"
    assert calls["task"] == "delete the temporary report"
    assert calls["confirmation_token"] == "token-123"
    assert calls["model"] == "test-model"
    assert calls["history"] == []
