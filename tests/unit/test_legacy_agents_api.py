import pytest

import agents_legacy_api


@pytest.mark.asyncio
async def test_legacy_run_rejects_unknown_agent():
    with pytest.raises(agents_legacy_api.HTTPException) as exc:
        await agents_legacy_api.run_agent_oneshot("unknown", "do something")

    assert exc.value.status_code == 400
    assert "unknown agent" in exc.value.detail


@pytest.mark.asyncio
async def test_legacy_endpoint_rejects_invalid_timeout():
    with pytest.raises(agents_legacy_api.HTTPException) as exc:
        await agents_legacy_api.run_agent(
            {"agent": "bill", "task": "hello", "timeout": "not-a-number"}
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "timeout must be a number"
