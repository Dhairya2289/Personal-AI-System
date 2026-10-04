"""Read-only API for the configured Obsidian vault."""

from __future__ import annotations

import html as _html
import re as _re
from typing import Any

from fastapi import APIRouter, HTTPException

import config

router = APIRouter(prefix="/api/obsidian", tags=["obsidian"])
OBSIDIAN_VAULT = config.OBSIDIAN_VAULT

def _md_to_html(text: str) -> str:
    """Very small markdown→HTML pass: paragraphs, headings, code blocks, lists,
    bold, italic, inline code, links. Enough to render an Obsidian note in the
    dashboard without pulling in a full markdown lib."""
    # Escape first
    out = _html.escape(text)
    # Code blocks ```lang\n...\n```
    out = _re.sub(
        r"```([\w-]*)\n(.*?)```",
        lambda m: f'<pre class="md-pre"><code class="md-code md-code-lang-{_html.escape(m.group(1) or "txt")}">{m.group(2)}</code></pre>',
        out,
        flags=_re.DOTALL,
    )
    # Inline code `...`
    out = _re.sub(r"`([^`\n]+)`", r"<code class=\"md-code\">\1</code>", out)
    # Headings
    for level in range(6, 0, -1):
        prefix = "#" * level
        out = _re.sub(
            rf"^{prefix}\s+(.+)$",
            rf'<h{level} class="md-h md-h{level}">\1</h{level}>',
            out,
            flags=_re.MULTILINE,
        )
    # Bold **...**  Italic *...* or _..._
    out = _re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", out)
    out = _re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    out = _re.sub(r"(?<![_w])_([^_\n]+)_(?![_w])", r"<em>\1</em>", out)
    # Wiki-links [[Note Name]] and [[Note|Display]]
    out = _re.sub(
        r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]",
        lambda m: f'<a class="md-wikilink" href="#" data-note="{_html.escape(m.group(1).strip())}">{_html.escape((m.group(2) or m.group(1)).strip())}</a>',
        out,
    )
    # Markdown links [text](href)
    out = _re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a class="md-link" href="\2" target="_blank" rel="noopener">\1</a>', out)
    # Blockquote
    out = _re.sub(r"^&gt;\s?(.+)$", r'<blockquote class="md-quote">\1</blockquote>', out, flags=_re.MULTILINE)
    # Unordered list (simple)
    out = _re.sub(r"^[-*]\s+(.+)$", r'<li class="md-li">\1</li>', out, flags=_re.MULTILINE)
    out = _re.sub(r"((?:<li class=\"md-li\">.*?</li>\n?)+)", r"<ul class=\"md-ul\">\1</ul>", out)
    # Paragraphs: split on blank lines for anything that isn't a block element
    blocks: list[str] = []
    buf: list[str] = []
    for line in out.split("\n"):
        if not line.strip():
            if buf:
                joined = " ".join(buf).strip()
                if joined and not joined.lstrip().startswith(("<h", "<pre", "<ul", "<blockquote", "<li")):
                    blocks.append(f"<p class=\"md-p\">{joined}</p>")
                else:
                    blocks.append(joined)
                buf = []
        else:
            buf.append(line)
    if buf:
        joined = " ".join(buf).strip()
        if joined and not joined.lstrip().startswith(("<h", "<pre", "<ul", "<blockquote", "<li")):
            blocks.append(f"<p class=\"md-p\">{joined}</p>")
        else:
            blocks.append(joined)
    return "\n".join(blocks)


@router.get("/status")
async def obsidian_status() -> dict[str, Any]:
    """Return whether the configured vault exists and how many notes it contains."""
    if not OBSIDIAN_VAULT.is_dir():
        return {"ok": False, "path": str(OBSIDIAN_VAULT), "error": "vault not found"}
    notes = [p for p in OBSIDIAN_VAULT.rglob("*.md")]
    folders = sorted({p.parent.relative_to(OBSIDIAN_VAULT).as_posix() for p in notes})
    return {
        "ok": True,
        "path": str(OBSIDIAN_VAULT),
        "name": OBSIDIAN_VAULT.name,
        "note_count": len(notes),
        "folders": folders,
    }


@router.get("/notes")
async def obsidian_list_notes(q: str = "", folder: str = "", limit: int = 200) -> dict[str, Any]:
    """List notes in the vault, optionally filtered by folder and/or substring.

    Returns relative path, name (without .md), folder, size, mtime.
    """
    if not OBSIDIAN_VAULT.is_dir():
        raise HTTPException(status_code=404, detail=f"vault not found: {OBSIDIAN_VAULT}")
    notes: list[dict[str, Any]] = []
    q_lower = q.lower()
    folder_prefix = folder.strip("/")
    for p in OBSIDIAN_VAULT.rglob("*.md"):
        rel = p.relative_to(OBSIDIAN_VAULT)
        rel_str = rel.as_posix()
        if folder_prefix and not rel_str.startswith(folder_prefix):
            continue
        name = p.stem
        if q_lower and q_lower not in name.lower() and q_lower not in rel_str.lower():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        notes.append({
            "path": rel_str,
            "name": name,
            "folder": rel.parent.as_posix(),
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    notes.sort(key=lambda n: n["mtime"], reverse=True)
    return {
        "count": len(notes),
        "notes": notes[:limit],
        "truncated": len(notes) > limit,
    }


@router.get("/notes/{path:path}")
async def obsidian_read_note(path: str) -> dict[str, Any]:
    """Read a single note, returning raw markdown, html-rendered preview, and
    frontmatter (if any)."""
    # Strip leading slash to avoid escape issues
    safe_rel = path.lstrip("/")
    # Defence-in-depth: reject any traversal — even though rglob is not used here.
    if ".." in safe_rel.split("/"):
        raise HTTPException(status_code=400, detail="invalid path")
    full = (OBSIDIAN_VAULT / safe_rel).resolve()
    if not full.is_relative_to(OBSIDIAN_VAULT.resolve()):
        raise HTTPException(status_code=400, detail="path escapes vault")
    if not full.is_file():
        raise HTTPException(status_code=404, detail=f"note not found: {safe_rel}")
    try:
        raw = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"read failed: {e}")
    frontmatter: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        # Pull off YAML frontmatter if present
        end = raw.find("\n---", 3)
        if end != -1:
            header = raw[3:end].strip()
            body = raw[end + 4 :].lstrip("\n")
            for line in header.split("\n"):
                if ":" in line and not line.startswith(" ") and not line.startswith("-"):
                    k, _, v = line.partition(":")
                    frontmatter[k.strip()] = v.strip().strip('"').strip("'")
    return {
        "path": safe_rel,
        "name": full.stem,
        "raw": raw,
        "html": _md_to_html(body),
        "frontmatter": frontmatter,
        "size": full.stat().st_size,
        "mtime": int(full.stat().st_mtime),
    }


@router.get("/search")
async def obsidian_search(q: str = "", limit: int = 30) -> dict[str, Any]:
    """Substring search across all notes; returns snippets around each match."""
    if not q.strip():
        return {"query": q, "count": 0, "results": []}
    if not OBSIDIAN_VAULT.is_dir():
        raise HTTPException(status_code=404, detail=f"vault not found: {OBSIDIAN_VAULT}")
    results: list[dict[str, Any]] = []
    q_lower = q.lower()
    for p in OBSIDIAN_VAULT.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text_lower = text.lower()
        idx = text_lower.find(q_lower)
        if idx == -1:
            continue
        # Build a snippet around the first match
        start = max(0, idx - 60)
        end = min(len(text), idx + len(q) + 100)
        snippet = text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        # Highlight the match
        highlighted = (
            _html.escape(snippet[: idx - start])
            + "<mark>"
            + _html.escape(snippet[idx - start : idx - start + len(q)])
            + "</mark>"
            + _html.escape(snippet[idx - start + len(q) :])
        )
        results.append({
            "path": p.relative_to(OBSIDIAN_VAULT).as_posix(),
            "name": p.stem,
            "snippet": snippet.strip(),
            "highlighted": highlighted,
            "match_count": text_lower.count(q_lower),
        })
        if len(results) >= limit:
            break
    results.sort(key=lambda r: r["match_count"], reverse=True)
    return {"query": q, "count": len(results), "results": results}


# Wikilink matcher reused by the graph builder: [[Target]], [[Target|alias]],
# [[Target#heading]]. We only care about the target (before | and #).
_WIKILINK_RE = _re.compile(r"\[\[([^\[\]]+?)\]\]")


@router.get("/graph")
def obsidian_graph(limit: int = 2000) -> dict[str, Any]:
    """Vault link graph — the classic Obsidian graph, rendered as a neural net.

    One node per ``.md`` note, one undirected edge per ``[[wikilink]]`` that
    resolves to another note. Each node carries ``deg`` (link count → hub size),
    ``folder`` (cluster colour) and file metadata. Read-only; never mutates the
    vault. Hidden dot-folders (``.obsidian``, ``.trash``, ``.gemini`` …) skipped.

    Declared ``def`` (not ``async``): it walks + reads every note, so Starlette
    runs it in a threadpool and the event loop is never blocked.
    """
    if not OBSIDIAN_VAULT.is_dir():
        return {"ok": False, "path": str(OBSIDIAN_VAULT), "error": "vault not found",
                "nodes": [], "links": [], "folders": [],
                "stats": {"notes": 0, "links": 0, "orphans": 0}}

    raw: list[dict[str, Any]] = []
    by_stem: dict[str, str] = {}        # lower basename          -> node id (first wins)
    by_path: dict[str, str] = {}        # lower rel path (no .md)  -> node id (exact)
    # sorted() so "first basename wins" is stable across machines / filesystems.
    for p in sorted(OBSIDIAN_VAULT.rglob("*.md")):
        rel = p.relative_to(OBSIDIAN_VAULT)
        if any(part.startswith(".") for part in rel.parts):
            continue
        nid = rel.as_posix()
        try:
            st = p.stat()
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        # Pull just the wikilink targets now; never retain the full body so peak
        # memory is O(#links), not O(sum of note sizes).
        targets: list[str] = []
        for hit in _WIKILINK_RE.findall(text):
            tgt = hit.split("|", 1)[0].split("#", 1)[0].strip()
            if tgt:
                targets.append(tgt)
        raw.append({"id": nid, "label": p.stem, "_dir": rel.parts[:-1],
                    "size": size, "mtime": mtime, "_targets": targets})
        by_stem.setdefault(p.stem.lower(), nid)
        by_path[nid[:-3].lower()] = nid          # strip the ".md" suffix
        if len(raw) >= limit:
            break

    # Cluster colouring: strip the directory prefix shared by EVERY note (vaults
    # commonly wrap everything in one wrapper dir like "Workflow/") so the real
    # categories become the clusters. A note sitting inside the shared prefix
    # itself is named by its own deepest folder via min(cpl, len-1) — never
    # collapsed to "(root)" (only genuine vault-root notes get that), so the
    # brain never goes one-colour while real sub-folders exist.
    dirs = [n["_dir"] for n in raw]
    cpl = 0
    if dirs:
        shortest = min(len(d) for d in dirs)
        while cpl < shortest and all(d[cpl] == dirs[0][cpl] for d in dirs):
            cpl += 1
    for n in raw:
        d = n["_dir"]
        n["folder"] = d[min(cpl, len(d) - 1)] if d else "(root)"

    links: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    degree: dict[str, int] = {n["id"]: 0 for n in raw}
    for n in raw:
        resolved: set[str] = set()
        for tgt in n["_targets"]:
            t = tgt.lower()
            # Obsidian precedence: exact path, then bare basename, then the
            # basename of a path-qualified link ([[folder/Note]]).
            tid = by_path.get(t) or by_stem.get(t)
            if tid is None and "/" in t:
                tid = by_stem.get(t.rsplit("/", 1)[-1])
            if tid and tid != n["id"]:
                resolved.add(tid)
        for tid in resolved:
            key = (n["id"], tid) if n["id"] < tid else (tid, n["id"])
            if key in seen:
                continue
            seen.add(key)
            links.append({"source": n["id"], "target": tid})
            degree[n["id"]] += 1
            degree[tid] += 1

    nodes = [{
        "id": n["id"], "label": n["label"], "folder": n["folder"],
        "deg": degree.get(n["id"], 0), "size": n["size"], "mtime": n["mtime"],
    } for n in raw]
    folders = sorted({n["folder"] for n in raw})
    orphans = sum(1 for nd in nodes if nd["deg"] == 0)

    return {"ok": True, "path": str(OBSIDIAN_VAULT), "name": OBSIDIAN_VAULT.name,
            "nodes": nodes, "links": links, "folders": folders,
            "stats": {"notes": len(nodes), "links": len(links), "orphans": orphans}}


