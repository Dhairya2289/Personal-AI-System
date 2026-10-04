import pytest

import app.search_api as search


@pytest.fixture
def search_dirs(tmp_path, monkeypatch):
    obsidian = tmp_path / "Obsidian"
    subjects = tmp_path / "subjects"
    obsidian.mkdir()
    subjects.mkdir()

    (obsidian / "Physics.md").write_text(
        "# Kinematics\nVelocity and acceleration", encoding="utf-8"
    )
    (subjects / "physics" / "notes").mkdir(parents=True)
    (subjects / "physics" / "notes" / "motion.md").write_text(
        "Acceleration is change in velocity.", encoding="utf-8"
    )
    (subjects / "physics" / "notes" / "quiz_notes.md").write_text(
        "Acceleration quiz", encoding="utf-8"
    )

    monkeypatch.setattr(search, "OBSIDIAN_VAULT", obsidian)
    monkeypatch.setattr(search, "SUBJECTS_DIR", subjects)


@pytest.mark.asyncio
async def test_global_search_ranks_name_match_and_ignores_quiz_files(search_dirs):
    result = await search.global_search("physics", limit=10)

    assert result["count"] >= 1
    assert result["results"][0]["kind"] == "obsidian"
    assert not any("quiz_notes" == item["title"] for item in result["results"])


@pytest.mark.asyncio
async def test_global_search_empty_query(search_dirs):
    assert await search.global_search("   ") == {
        "query": "   ",
        "count": 0,
        "results": [],
    }
