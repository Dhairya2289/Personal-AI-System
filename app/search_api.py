"""Cross-source dashboard search API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

import config

router = APIRouter(prefix="/api/search", tags=["search"])
OBSIDIAN_VAULT = config.OBSIDIAN_VAULT
SUBJECTS_DIR = config.SUBJECTS_DIR


@router.get("/global")
async def global_search(q: str = "", limit: int = 25) -> dict[str, Any]:
    """Rank results across Obsidian and subject notes."""
    if not q.strip():
        return {"query": q, "count": 0, "results": []}

    ql = q.lower()
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(
        kind: str,
        key: str,
        title: str,
        subtitle: str,
        score: float,
        deep_link: str = "",
    ) -> None:
        if (kind, key) in seen:
            return
        seen.add((kind, key))
        results.append(
            {
                "kind": kind,
                "id": key,
                "title": (title or "")[:140],
                "subtitle": (subtitle or "")[:200],
                "score": round(score, 3),
                "deep_link": deep_link,
            }
        )

    if OBSIDIAN_VAULT.is_dir():
        for p in OBSIDIAN_VAULT.rglob("*.md"):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = p.stem
            path_str = p.relative_to(OBSIDIAN_VAULT).as_posix()
            score = 0.0
            if ql in name.lower():
                score = max(score, 0.9)
            if ql in path_str.lower():
                score = max(score, 0.5)
            if ql in text[:4096].lower():
                score = max(score, 0.6)
            if score > 0:
                idx = text.lower().find(ql)
                snippet = ""
                if idx != -1:
                    start = max(0, idx - 50)
                    end = min(len(text), idx + 120)
                    snippet = text[start:end].strip()
                add(
                    "obsidian",
                    path_str,
                    name,
                    f"{p.parent.name}/  ·  Obsidian"
                    + (f"  ·  {snippet[:80]}" if snippet else ""),
                    score,
                    deep_link="obsidian-vault",
                )
            if len(results) >= limit * 2:
                break

    if SUBJECTS_DIR.is_dir():
        for p in SUBJECTS_DIR.rglob("*.md"):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "quiz" in p.name.lower():
                continue
            name = p.stem
            rel = p.relative_to(SUBJECTS_DIR)
            score = 0.0
            if ql in name.lower() or ql in rel.as_posix().lower():
                score = max(score, 0.7)
            if ql in text[:2048].lower():
                score = max(score, 0.4)
            if score > 0:
                add(
                    "subject-note",
                    rel.as_posix(),
                    name,
                    f"Subject notes  ·  {rel.parent.as_posix()}",
                    score,
                    deep_link="library-notes",
                )
            if len(results) >= limit * 2:
                break

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"query": q, "count": len(results), "results": results[:limit]}
