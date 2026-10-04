"""Daily Mission Briefing API."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter

import config

router = APIRouter(prefix="/api/briefing", tags=["briefing"])
OBSIDIAN_VAULT = config.OBSIDIAN_VAULT
_BRIEFING_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_BRIEFING_TTL = 3600.0

# ===========================================================================
# Daily Mission Briefing
# ---------------------------------------------------------------------------
# Server-side: pulls today's study data + yesterday's Obsidian daily note +
# recently-added Open Notebook sources, asks the configured LLM to produce
# a 5-bullet brief with a suggested focus, and caches the result for 1 h.
# Returns markdown so the frontend can render it as-is.
# ===========================================================================

_BRIEFING_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_BRIEFING_TTL = 3600.0  # 1 hour


async def _build_briefing() -> dict[str, Any]:
    """Assemble today's data and return markdown + meta.

    Notebook counts come best-effort from the NotebookLM CLI; the brief itself is
    assembled deterministically (NotebookLM answers against sources, not free
    prompts, so there is no drop-in remote LLM for an arbitrary briefing call)."""
    today = datetime.now().astimezone()
    today_iso = today.date().isoformat()
    yesterday_iso = (today.date() - timedelta(days=1)).isoformat()

    # NotebookLM notebook count (best-effort; never blocks the brief)
    nlm_notebooks = 0
    try:
        from notebooklm import _parse_json as _nlm_parse
        from notebooklm import _run as _nlm_run
        rc, out, _err = await _nlm_run(["list", "--json"], timeout=10.0)
        if rc == 0:
            ok, data = _nlm_parse(out)
            if ok:
                nbs = data.get("notebooks", data) if isinstance(data, dict) else data
                nlm_notebooks = len(nbs or [])
    except (OSError, ValueError, KeyError, TypeError):
        pass

    # Obsidian — yesterday's daily note (best guess from filename)
    obs_yesterday_snippet = ""
    if OBSIDIAN_VAULT.is_dir():
        for p in OBSIDIAN_VAULT.rglob("*.md"):
            if yesterday_iso in p.name:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                    obs_yesterday_snippet = text[:1200]
                    break
                except OSError:
                    continue

    # Deterministic briefing (always available)
    bullets: list[str] = []
    if nlm_notebooks:
        bullets.append(
            f"**{nlm_notebooks} notebooks** are live in NotebookLM \u2014 open the NotebookLM tab "
            "to chat with your sources or generate a podcast, quiz, or report."
        )
    else:
        bullets.append(
            "No NotebookLM notebooks detected yet \u2014 create one from the NotebookLM tab "
            "and add your first sources."
        )
    if obs_yesterday_snippet:
        bullets.append("Yesterday's Obsidian note is loaded \u2014 pick one open thread to close before adding new ones.")
    else:
        bullets.append("No note dated yesterday in the Obsidian vault \u2014 start today's daily note and lock in one win.")
    bullets.append("Heatmap for today is empty so far; a single completed study block is enough to light it up.")
    bullets.append("**Suggested first task:** open the NotebookLM Chat tab and ask a question from your most recent source.")
    markdown = "\n\n".join([f"- {b}" for b in bullets])

    return {
        "date": today_iso,
        "generated_at": today.isoformat(timespec="seconds"),
        "markdown": markdown,
        "stats": {
            "notebooks": nlm_notebooks,
            "obsidian_yesterday_excerpt_chars": len(obs_yesterday_snippet),
        },
    }


@router.get("/today")
async def briefing_today(refresh: bool = False) -> dict[str, Any]:
    """Return today's daily mission briefing. Cached for 1h by default;
    pass ?refresh=1 to force a rebuild."""
    key = datetime.now().astimezone().date().isoformat()
    now = time.time()
    cached = _BRIEFING_CACHE.get(key)
    if not refresh and cached and (now - cached[0]) < _BRIEFING_TTL:
        return cached[1]
    payload = await _build_briefing()
    _BRIEFING_CACHE[key] = (now, payload)
    # Keep only today's entry to avoid unbounded growth
    _BRIEFING_CACHE.clear()
    _BRIEFING_CACHE[key] = (now, payload)
    return payload


