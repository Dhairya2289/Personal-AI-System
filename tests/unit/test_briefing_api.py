import pytest

import app.briefing_api as briefing


@pytest.mark.asyncio
async def test_briefing_builds_without_notebooklm(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing, "OBSIDIAN_VAULT", tmp_path)

    async def fake_run(*args, **kwargs):
        return 0, '{"notebooks": []}', ""

    monkeypatch.setattr(briefing, "_nlm_run", fake_run)
    monkeypatch.setattr(briefing, "_nlm_parse", lambda raw: (True, {"notebooks": []}))
    briefing._BRIEFING_CACHE.clear()

    result = await briefing.briefing_today(refresh=True)

    assert result["stats"]["notebooks"] == 0
    assert result["markdown"]
    assert "date" in result
