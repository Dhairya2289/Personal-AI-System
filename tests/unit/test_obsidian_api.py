import pytest
from fastapi import HTTPException

import app.obsidian_api as obsidian


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "Folder").mkdir(parents=True)
    (root / "Folder" / "Alpha.md").write_text(
        "# Alpha\n\nSee [[Beta]].", encoding="utf-8"
    )
    (root / "Beta.md").write_text(
        "---\ntopic: test\n---\n# Beta\n\nContent.", encoding="utf-8"
    )
    monkeypatch.setattr(obsidian, "OBSIDIAN_VAULT", root)
    return root


@pytest.mark.asyncio
async def test_obsidian_list_and_read(vault):
    listed = await obsidian.obsidian_list_notes()
    assert listed["count"] == 2
    assert {n["name"] for n in listed["notes"]} == {"Alpha", "Beta"}

    note = await obsidian.obsidian_read_note("Folder/Alpha.md")
    assert note["name"] == "Alpha"
    assert "md-h1" in note["html"]


@pytest.mark.asyncio
async def test_obsidian_search(vault):
    result = await obsidian.obsidian_search("content")
    assert result["count"] == 1
    assert result["results"][0]["name"] == "Beta"


@pytest.mark.asyncio
async def test_obsidian_rejects_path_traversal(vault):
    with pytest.raises(HTTPException) as exc:
        await obsidian.obsidian_read_note("../outside.md")
    assert exc.value.status_code == 400


def test_obsidian_graph(vault):
    graph = obsidian.obsidian_graph()
    assert graph["stats"]["notes"] == 2
    assert graph["stats"]["links"] == 1
