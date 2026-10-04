"""
Unit tests for stats and executive overview.
"""
from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

import stats


@pytest.mark.asyncio
async def test_executive_overview_schema():
    resp: JSONResponse = await stats.executive_overview()
    assert resp.status_code == 200
    data = json.loads(resp.body.decode("utf-8"))

    assert "date" in data
    assert "completion_pct" in data
    assert "study" in data
    assert "tasks" in data
    assert "memory" in data
    assert "ai_ops" in data
    assert isinstance(data["memory"]["total"], int)
    assert isinstance(data["ai_ops"]["circuit_status"], str)
