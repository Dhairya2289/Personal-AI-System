"""
Mission Control Dashboard — FastAPI backend.

Private service. Binds 127.0.0.1:51763 only. Reach from a laptop via SSH tunnel.
The dashboard reads study data and can trigger agents, so the port stays closed
to the network on purpose.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shutil
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
from app.runtime.process import communicate_with_timeout
from app.runtime.hermes import build_agent_env
from app.storage.dashboard_db import db_connection, init_dashboard_dbs
from app.obsidian_api import router as obsidian_router
from app.search_api import router as search_router
from app.briefing_api import router as briefing_router
from app.practice_api import router as practice_router

# ---------------------------------------------------------------------------
# Paths — all resolved in config.py from environment variables (see .env.example).
# Names kept identical to their historical values so the rest of this module is
# unchanged; only the source of truth moved.
# ---------------------------------------------------------------------------
HERMES_PYTHON = config.HERMES_PYTHON
HERMES_HOME_BILL = config.HERMES_HOME
PROFILES_DIR = config.PROFILES_DIR
AGENT_LOG_DB = config.AGENT_LOG_DB
SUBJECTS_DIR = config.SUBJECTS_DIR
RESEARCH_DB = config.RESEARCH_DB
QUIZ_DB = config.QUIZ_DB
FLASHCARD_DB = config.FLASHCARD_DB
PRODUCTIVITY_DB = config.PRODUCTIVITY_DB
CHAT_DB = config.CHAT_DB
RESEARCH_DIR = config.RESEARCH_DIR
PLANNING_DIR = config.PLANNING_DIR
PLANNING_DIR.mkdir(parents=True, exist_ok=True)

DASHBOARD_DIR = Path(__file__).resolve().parent
STATIC_DIR = DASHBOARD_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# Obsidian vault — point to the canonical vault on this machine. We surface notes
# through the dashboard so the user can browse, search, and read without booting
# the Obsidian desktop app.
# Obsidian vault surfaced through the Obsidian / Brain tabs. Override anywhere
# with $OBSIDIAN_VAULT (see config.py).
OBSIDIAN_VAULT = config.OBSIDIAN_VAULT

PIPELINE_SCRIPT = config.PIPELINE_SCRIPT
PIPELINE_LOG_DIR = DASHBOARD_DIR / "logs" / "pipelines"
PIPELINE_LOG_DIR.mkdir(parents=True, exist_ok=True)
_pipeline_jobs: dict[str, dict[str, Any]] = {}

# Design reference: canonical visual source of truth for the whole project.
# Every later visual build (page, card, modal, chart, nav, etc.) must study
# this template first and match its design language before touching code.
DESIGN_REF_DIR = DASHBOARD_DIR / "design-reference"
DESIGN_REF_TEMPLATE = DESIGN_REF_DIR / "template.html"
DESIGN_REF_ARCHIVE = DESIGN_REF_DIR / "archive"
DESIGN_REF_SCREENSHOTS = DESIGN_REF_DIR / "screenshots"
DESIGN_REF_PAGE = STATIC_DIR / "design-reference.html"

# Extension policy for uploads.
HTML_EXTS = {".html", ".htm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB per file

# Profile avatar — one user-uploaded photo, served as a static asset and shown
# in place of the "D" initial in the app bar.
PROFILE_DIR = STATIC_DIR / "profile"

# Ensure storage exists at import time so endpoints don't race on first upload.
for _d in (DESIGN_REF_DIR, DESIGN_REF_ARCHIVE, DESIGN_REF_SCREENSHOTS, PROFILE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

VERSION = "1.7.0"

# Canonical per-agent identity. Used by every overview panel + the rest of the app.
AGENT_REGISTRY: list[dict[str, Any]] = [
    # Volt palette — each agent carries a companion hue with intent:
    # bill=lime (brand/coordinator hub) · vault=cyan (info) · scholar=emerald (growth)
    # quizmaster=violet (category) · planner=amber (due) · dev=coral (infra/alert)
    {"key": "bill",       "name": "Bill",       "icon": "◎", "emoji": "🧭", "color": "#c8ff00", "role": "Coordinator"},
    {"key": "vault",      "name": "Vault",      "icon": "◆", "emoji": "📁", "color": "#36d6e7", "role": "File Librarian"},
    {"key": "scholar",    "name": "Scholar",    "icon": "✧", "emoji": "📚", "color": "#2be08a", "role": "Notes & Research"},
    {"key": "quizmaster", "name": "Quizmaster", "icon": "◈", "emoji": "🎯", "color": "#b08cff", "role": "Quiz & Flashcards"},
    {"key": "planner",    "name": "Planner",    "icon": "◌", "emoji": "📅", "color": "#ffc24b", "role": "Schedule Manager"},
    {"key": "dev",        "name": "Dev",        "icon": "✦", "emoji": "🛠", "color": "#ff6b81", "role": "Infrastructure"},
]
AGENT_BY_KEY = {a["key"]: a for a in AGENT_REGISTRY}

# Bill is the coordinator and lives at the top-level Hermes home, not under profiles/.
KNOWN_AGENTS = {"bill", "vault", "scholar", "quizmaster", "planner", "dev"}


async def _run_cmd_json(cmd: list[str], *, timeout: float = 12.0) -> tuple[Any | None, str, int | None]:
    """Run a local CLI command and parse the first JSON object/array it prints."""
    if not cmd or shutil.which(cmd[0]) is None:
        return None, f"{cmd[0] if cmd else 'command'} not found", None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b, timed_out = await communicate_with_timeout(proc, timeout)
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if timed_out:
            return None, "timed out", proc.returncode
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout).strip()
        start_positions = [p for p in (text.find("{"), text.find("[")) if p >= 0]
        if start_positions:
            try:
                return json.loads(text[min(start_positions):]), stderr.strip(), proc.returncode
            except json.JSONDecodeError:
                pass
        return None, (stderr or stdout).strip(), proc.returncode
    except Exception as exc:
        return None, str(exc), None


async def _run_cmd_text(cmd: list[str], *, timeout: float = 12.0) -> tuple[str, str, int | None]:
    if not cmd or shutil.which(cmd[0]) is None:
        return "", f"{cmd[0] if cmd else 'command'} not found", None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b, timed_out = await _communicate_with_timeout(proc, timeout)
        if timed_out:
            return stdout_b.decode("utf-8", errors="replace"), "timed out", proc.returncode
        return (
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
            proc.returncode,
        )
    except Exception as exc:
        return "", str(exc), None


def _pipeline_preflight() -> list[str]:
    """Return startup problems that would make the upload pipeline fail."""
    problems: list[str] = []
    if not HERMES_HOME_BILL.is_dir():
        problems.append(f"HERMES_HOME not found: {HERMES_HOME_BILL}")
    if not HERMES_PYTHON.is_file():
        problems.append(f"Hermes Python not found: {HERMES_PYTHON}")
    if not PIPELINE_SCRIPT.is_file():
        problems.append(f"pipeline script not found: {PIPELINE_SCRIPT}")
    for agent in ("vault", "scholar", "quizmaster", "planner"):
        profile = PROFILES_DIR / agent
        if not profile.is_dir():
            problems.append(f"profile for '{agent}' not found: {profile}")
    return problems


def _compact_tail(text: str, *, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


async def _watch_pipeline_job(job_id: str, proc: asyncio.subprocess.Process, log_path: Path) -> None:
    job = _pipeline_jobs.get(job_id)
    if not job:
        return
    job["status"] = "running"
    try:
        rc = await proc.wait()
        job["returncode"] = rc
        job["status"] = "complete" if rc == 0 else "failed"
    except Exception as e:  # noqa: BLE001 - background status should never disappear
        job["status"] = "failed"
        job["error"] = repr(e)
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        try:
            job["log_tail"] = _compact_tail(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            job["log_tail"] = ""



def _db(path, *, timeout: float = 10.0) -> sqlite3.Connection:
    return db_connection(path, timeout=timeout)


init_dashboard_dbs(
    quiz_db=QUIZ_DB,
    flashcard_db=FLASHCARD_DB,
    productivity_db=PRODUCTIVITY_DB,
    chat_db=CHAT_DB,
)

# Agent Discord channel IDs.
#
# Populated from environment so install-specific channel snowflakes never sit in
# the repo. Format: MC_DISCORD_CHANNELS="vault:1234,scholar:5678,…". Leave unset
# (the default) to skip Discord routing — chat still works in-app.
def _parse_agent_channels(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece or ":" not in piece:
            continue
        k, _, v = piece.partition(":")
        if k.strip() and v.strip().isdigit():
            out[k.strip()] = v.strip()
    return out


AGENT_DISCORD_CHANNELS: dict[str, str] = _parse_agent_channels(
    os.environ.get("MC_DISCORD_CHANNELS", "")
)

# In-memory tracking of which agents have a background turn running.
_chat_running: dict[str, bool] = {}
_chat_running_lock = asyncio.Lock()


async def _set_chat_running(agent: str, running: bool) -> None:
    async with _chat_running_lock:
        _chat_running[agent] = running


def _is_chat_running(agent: str) -> bool:
    return _chat_running.get(agent, False)


# ---------------------------------------------------------------------------
# Mirroring helpers — post as the bot so the gateway ignores the message.
# ---------------------------------------------------------------------------
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _read_discord_token() -> str:
    env_file = config.ENV_FILE
    if not env_file.is_file():
        return ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _read_telegram_creds() -> tuple[str, str]:
    env_file = config.ENV_FILE
    if not env_file.is_file():
        return "", ""
    token = ""
    chat = ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("TELEGRAM_HOME_CHANNEL="):
                chat = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return token, chat


async def _mirror_to_discord(channel_id: str, text: str) -> bool:
    token = _read_discord_token()
    if not token or not channel_id:
        return False
    payload = json.dumps({"content": text[:1999]}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=payload,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": _BROWSER_UA,
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


async def _mirror_to_telegram(chat_id: str, text: str) -> bool:
    token, _ = _read_telegram_creds()
    if not token or not chat_id:
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4095]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _BROWSER_UA},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


async def _mirror_message(agent: str, role: str, text: str) -> None:
    label = "👤" if role == "user" else "🤖"
    mirror_text = f"{label} **{agent.capitalize()}**\n{text}"
    if agent == "bill":
        _, chat_id = _read_telegram_creds()
        if chat_id:
            await _mirror_to_telegram(chat_id, mirror_text)
    else:
        channel_id = AGENT_DISCORD_CHANNELS.get(agent, "")
        if channel_id:
            await _mirror_to_discord(channel_id, mirror_text)


def _get_chat_history(agent: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = _db(CHAT_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT id, agent, role, text, created_at FROM chat_messages WHERE agent = ? ORDER BY id DESC LIMIT ?",
        (agent, limit),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def _trim_chat_history(agent: str, keep: int = 200) -> None:
    conn = _db(CHAT_DB)
    c = conn.cursor()
    c.execute(
        "DELETE FROM chat_messages WHERE agent = ? AND id NOT IN (SELECT id FROM chat_messages WHERE agent = ? ORDER BY id DESC LIMIT ?)",
        (agent, agent, keep),
    )
    conn.commit()
    conn.close()


def _store_chat_message(agent: str, role: str, text: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    conn = _db(CHAT_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "INSERT INTO chat_messages (agent, role, text, created_at) VALUES (?, ?, ?, ?) RETURNING id",
        (agent, role, text, now),
    )
    row_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    _trim_chat_history(agent)
    return {"id": row_id, "agent": agent, "role": role, "text": text, "created_at": now}


async def _run_agent_chat_turn(agent: str, user_text: str) -> None:
    """Background task: runs the agent with full conversation context, stores the reply, and mirrors it."""
    await _set_chat_running(agent, True)
    try:
        # Build context from chat history (last 30 messages)
        history = _get_chat_history(agent, limit=30)
        context_lines = []
        for msg in history:
            sender = config.USER_NAME if msg["role"] == "user" else agent.capitalize()
            context_lines.append(f"{sender}: {msg['text']}")

        system_prefix = (
            f"You are {agent.capitalize()}, a specialist agent in {config.USER_NAME}'s AI Student Companion team. "
            f"Respond directly and helpfully to the user's message. Keep responses concise but complete. "
            f"Do not use filler phrases like 'Great question' or 'Certainly'.\n\n"
            f"Conversation context (most recent first):\n"
            + "\n".join(context_lines) + "\n\n"
            + f"Now respond to the latest message as {agent.capitalize()}."
        )

        if agent == "bill":
            hermes_home = HERMES_HOME_BILL
        else:
            hermes_home = PROFILES_DIR / agent
        if not hermes_home.is_dir():
            _store_chat_message(agent, "assistant", f"[error] HERMES_HOME for '{agent}' not found.")
            return

        env = build_agent_env(
            hermes_home,
            hermes_home=hermes_home,
            agent_log_db=AGENT_LOG_DB,
        )
        provider_flag = env["HERMES_INFERENCE_PROVIDER"]
        model_flag = env["HERMES_INFERENCE_MODEL"]

        cmd = [
            str(HERMES_PYTHON),
            "-m", "hermes_cli.main",
            "-z", system_prefix,
            "--provider", provider_flag,
            "--model", model_flag,
            "--yolo", "--accept-hooks",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes, timed_out = await _communicate_with_timeout(proc, 600.0)
        if timed_out:
            reply_text = "[timeout] The agent took too long to respond. Try again or simplify your request."
            _store_chat_message(agent, "assistant", reply_text)
            await _mirror_message(agent, "assistant", reply_text)
            return

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Extract the agent's reply from stdout (last substantial paragraph)
        reply_text = stdout.strip()
        if not reply_text:
            reply_text = stderr.strip() or "[error] The agent produced no output."
        if len(reply_text) > 8000:
            reply_text = reply_text[:7997] + "..."

        _store_chat_message(agent, "assistant", reply_text)
        await _mirror_message(agent, "assistant", reply_text)
    except Exception as exc:
        err = f"[error] Agent turn failed: {exc}"
        _store_chat_message(agent, "assistant", err)
        await _mirror_message(agent, "assistant", err)
    finally:
        await _set_chat_running(agent, False)


def _update_research_status(id: int, status: str) -> None:
    """Flip a research row to a new status in research.db."""
    conn = _db(RESEARCH_DB)
    c = conn.cursor()
    c.execute("UPDATE research SET status = ? WHERE id = ?", (status, id))
    conn.commit()
    conn.close()


def _research_log_path(research_id: int) -> Path:
    return RESEARCH_DIR / ".logs" / f"research_{research_id}.log"


def _research_log_tail(research_id: int, *, limit: int = 4000) -> str:
    log_path = _research_log_path(research_id)
    if not log_path.is_file():
        return ""
    try:
        return _compact_tail(log_path.read_text(encoding="utf-8", errors="replace"), limit=limit)
    except OSError:
        return ""


def _refresh_research_row_status(row: dict[str, Any]) -> dict[str, Any]:
    """Synchronize a research row with its output file and per-run log."""
    filepath = RESEARCH_DIR / row["filename"]
    row["ready"] = filepath.is_file()
    row["log_path"] = str(_research_log_path(int(row["id"])))
    row["log_tail"] = _research_log_tail(int(row["id"]), limit=1200)
    if row["status"] == "researching":
        if row["ready"]:
            row["status"] = "complete"
            _update_research_status(int(row["id"]), "complete")
        elif "scholar exited rc=" in row["log_tail"] or "spawn failed:" in row["log_tail"]:
            row["status"] = "failed"
            _update_research_status(int(row["id"]), "failed")
    row["ready"] = row["status"] == "complete" and filepath.is_file()
    return row


app = FastAPI(title="Mission Control", version=VERSION)


# ---------------------------------------------------------------------------
# Security: same-origin enforcement (CSRF / cross-site WebSocket defense).
#
# The systemd unit binds this service to the Tailscale interface, so it is
# reachable by every device on the tailnet — and, more importantly, a malicious
# web page open in the user's browser could try to drive state-changing
# requests against it (CSRF) or open the /ws/terminal shell socket cross-site
# (CSWSH → a remote shell). Browsers attach an Origin header to cross-site
# requests and to every WebSocket handshake; a same-origin request from our own
# frontend carries Origin whose host:port equals the Host header. We refuse any
# state-changing HTTP request whose Origin is present and does not match Host.
#
# Requests with no Origin (curl, the headless localhost automation services such
# as hypr-autopomodoro / vault-reindex) are allowed — they are not browser-driven
# CSRF vectors. GET/HEAD/OPTIONS are reads and are left untouched; cross-origin
# *reads* are already unreadable to an attacker because we send no CORS headers.
# The /ws/terminal socket enforces its own same-origin check in terminal.py,
# because HTTP middleware does not see the WebSocket scope.
# ---------------------------------------------------------------------------
from urllib.parse import urlparse as _urlparse

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def _enforce_client_and_same_origin(request: Request, call_next):
    client_host = request.client.host if request.client else "127.0.0.1"
    import ipaddress
    def _is_safe(ip_str: str) -> bool:
        if ip_str in ("127.0.0.1", "::1", "localhost"):
            return True
        try:
            ip = ipaddress.ip_address(ip_str)
            return ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")
        except ValueError:
            return False
    if not _is_safe(client_host):
        return JSONResponse(
            {"detail": f"Access forbidden from client host '{client_host}': only localhost and Tailscale allowed"},
            status_code=403,
        )

    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and _urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse(
                {"detail": "cross-origin request refused"},
                status_code=403,
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Chat endpoints
# ---------------------------------------------------------------------------
@app.get("/api/agents")
async def list_agents() -> JSONResponse:
    """Return agent list with Bill first."""
    return JSONResponse({"agents": AGENT_REGISTRY})


@app.get("/api/chat/{agent}")
async def get_chat_history_endpoint(agent: str) -> JSONResponse:
    agent = agent.strip().lower()
    if agent not in KNOWN_AGENTS:
        raise HTTPException(status_code=400, detail=f"unknown agent '{agent}'")
    history = _get_chat_history(agent, limit=200)
    return JSONResponse({"agent": agent, "history": history, "is_running": _is_chat_running(agent)})


@app.post("/api/chat/{agent}")
async def send_chat_message(agent: str, request: Request) -> JSONResponse:
    agent = agent.strip().lower()
    if agent not in KNOWN_AGENTS:
        raise HTTPException(status_code=400, detail=f"unknown agent '{agent}'")
    payload = await request.json()
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    # Store user message
    msg = _store_chat_message(agent, "user", text)

    # Mirror user message immediately
    asyncio.create_task(_mirror_message(agent, "user", text))

    # Launch background turn if not already running
    if not _is_chat_running(agent):
        asyncio.create_task(_run_agent_chat_turn(agent, text))

    return JSONResponse({"ok": True, "message": msg, "is_running": True})


@app.post("/api/chat/{agent}/reset")
async def reset_chat(agent: str) -> JSONResponse:
    agent = agent.strip().lower()
    if agent not in KNOWN_AGENTS:
        raise HTTPException(status_code=400, detail=f"unknown agent '{agent}'")
    conn = _db(CHAT_DB)
    c = conn.cursor()
    c.execute("DELETE FROM chat_messages WHERE agent = ?", (agent,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "deleted": deleted})


@app.get("/")
async def root_index() -> FileResponse:
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail=f"index not found: {INDEX_FILE}")
    return FileResponse(INDEX_FILE)


# ---------------------------------------------------------------------------
# The one helper every feature funnels through.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Design reference — canonical visual source of truth.
# ---------------------------------------------------------------------------
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_basename(name: str) -> str:
    """Strip any directory component and reduce to [A-Za-z0-9._-]."""
    base = Path(name).name  # drops any path component, blocks traversal
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._-") or "file"
    # cap length to avoid filesystem oddities
    return cleaned[:160]


def _file_info(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size_bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


async def _save_upload_streaming(src: UploadFile, dst: Path) -> int:
    """Stream an UploadFile to disk in chunks, enforcing MAX_UPLOAD_BYTES. Returns bytes written."""
    written = 0
    async with aiofiles.open(dst, "wb") as out:
        while True:
            chunk = await src.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                await out.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"file '{src.filename}' exceeds {MAX_UPLOAD_BYTES} bytes",
                )
            await out.write(chunk)
    return written


@app.get("/api/design-reference")
async def design_reference_state() -> dict[str, Any]:
    """Report what is currently stored in the design-reference folder."""
    template = _file_info(DESIGN_REF_TEMPLATE) if DESIGN_REF_TEMPLATE.is_file() else None
    screenshots = [
        _file_info(p) for p in sorted(DESIGN_REF_SCREENSHOTS.iterdir())
        if p.is_file()
    ] if DESIGN_REF_SCREENSHOTS.is_dir() else []
    archive = [
        _file_info(p) for p in sorted(DESIGN_REF_ARCHIVE.iterdir(), reverse=True)
        if p.is_file()
    ] if DESIGN_REF_ARCHIVE.is_dir() else []
    return {
        "root": str(DESIGN_REF_DIR),
        "template": template,
        "screenshots": screenshots,
        "archive": archive,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "accepted": {
            "template": sorted(HTML_EXTS),
            "screenshots": sorted(IMAGE_EXTS),
        },
    }


@app.post("/api/design-reference/upload")
async def design_reference_upload(
    files: list[UploadFile] = File(..., description="HTML template and/or reference screenshots"),
) -> JSONResponse:
    """Accept one or more files. .html/.htm replace the canonical template
    (previous version is archived). Images go into screenshots/."""
    if not files:
        raise HTTPException(status_code=400, detail="no files provided")

    saved_template: dict[str, Any] | None = None
    saved_screenshots: list[dict[str, Any]] = []
    archived: dict[str, Any] | None = None
    rejected: list[dict[str, str]] = []

    for upload in files:
        original = upload.filename or ""
        ext = Path(original).suffix.lower()
        safe = _safe_basename(original)

        if ext in HTML_EXTS:
            # archive previous template if present, then write new one
            if DESIGN_REF_TEMPLATE.is_file():
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                archive_path = DESIGN_REF_ARCHIVE / f"template-{ts}.html"
                DESIGN_REF_TEMPLATE.replace(archive_path)
                archived = _file_info(archive_path)
            await _save_upload_streaming(upload, DESIGN_REF_TEMPLATE)
            saved_template = {**_file_info(DESIGN_REF_TEMPLATE), "uploaded_as": safe}

        elif ext in IMAGE_EXTS:
            dst = DESIGN_REF_SCREENSHOTS / safe
            # if name collides, suffix with timestamp instead of clobbering
            if dst.exists():
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                dst = DESIGN_REF_SCREENSHOTS / f"{dst.stem}-{ts}{dst.suffix}"
            await _save_upload_streaming(upload, dst)
            saved_screenshots.append(_file_info(dst))

        else:
            rejected.append({"name": original, "reason": f"extension '{ext}' not accepted"})

    if not saved_template and not saved_screenshots:
        raise HTTPException(
            status_code=400,
            detail={"message": "no accepted files in upload", "rejected": rejected},
        )

    return JSONResponse({
        "template": saved_template,
        "archived_previous": archived,
        "screenshots": saved_screenshots,
        "rejected": rejected,
    })


@app.get("/api/design-reference/template")
async def design_reference_template_view() -> FileResponse:
    if not DESIGN_REF_TEMPLATE.is_file():
        raise HTTPException(status_code=404, detail="no template uploaded yet")
    return FileResponse(DESIGN_REF_TEMPLATE, media_type="text/html")


@app.get("/api/design-reference/template/download")
async def design_reference_template_download() -> FileResponse:
    if not DESIGN_REF_TEMPLATE.is_file():
        raise HTTPException(status_code=404, detail="no template uploaded yet")
    return FileResponse(
        DESIGN_REF_TEMPLATE,
        media_type="application/octet-stream",
        filename="template.html",
    )


@app.get("/api/design-reference/screenshots/{name}")
async def design_reference_screenshot(name: str, download: bool = False) -> FileResponse:
    safe = _safe_basename(name)
    target = DESIGN_REF_SCREENSHOTS / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"screenshot not found: {safe}")
    if download:
        return FileResponse(target, media_type="application/octet-stream", filename=safe)
    return FileResponse(target)


@app.delete("/api/design-reference/screenshots/{name}")
async def design_reference_screenshot_delete(name: str) -> dict[str, Any]:
    safe = _safe_basename(name)
    target = DESIGN_REF_SCREENSHOTS / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"screenshot not found: {safe}")
    target.unlink()
    return {"deleted": safe}


@app.get("/design-reference")
async def design_reference_page() -> FileResponse:
    if not DESIGN_REF_PAGE.is_file():
        raise HTTPException(status_code=404, detail=f"page not found: {DESIGN_REF_PAGE}")
    return FileResponse(DESIGN_REF_PAGE)



@app.get("/api/tasks")
async def list_tasks(status: str | None = None) -> JSONResponse:
    """List tasks ordered by position."""
    conn = _db(PRODUCTIVITY_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if status:
        c.execute("SELECT * FROM tasks WHERE status = ? ORDER BY position ASC, created_at DESC", (status,))
    else:
        c.execute("SELECT * FROM tasks ORDER BY position ASC, created_at DESC")
    rows = [dict(r) for r in c.fetchall()]
    # counts per status
    c.execute("SELECT status, COUNT(*) as count FROM tasks GROUP BY status")
    counts = {r["status"]: r["count"] for r in c.fetchall()}
    conn.close()
    return JSONResponse({"items": rows, "counts": counts, "statuses": ["todo", "in_progress", "done"]})


@app.post("/api/tasks")
async def upsert_task(payload: dict[str, Any]) -> JSONResponse:
    """Create or update a task."""
    task_id = str(payload.get("id", "")).strip()
    title = str(payload.get("title", "")).strip()
    subject = str(payload.get("subject", "")).strip() or None
    status = str(payload.get("status", "todo")).strip()
    position = float(payload.get("position", 0.0))
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    if not task_id:
        import uuid
        task_id = uuid.uuid4().hex[:12]
    created_at = str(payload.get("created_at", "")).strip() or datetime.now(timezone.utc).isoformat()
    conn = _db(PRODUCTIVITY_DB)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (id, title, subject, status, position, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title=excluded.title, subject=excluded.subject, status=excluded.status, position=excluded.position",
        (task_id, title, subject, status, position, created_at),
    )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "id": task_id})


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str) -> JSONResponse:
    """Delete a task."""
    conn = _db(PRODUCTIVITY_DB)
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="task not found")
    return JSONResponse({"ok": True})



@app.get("/api/stickies")
async def list_stickies() -> JSONResponse:
    conn = _db(PRODUCTIVITY_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM stickies ORDER BY position ASC, created_at ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return JSONResponse({"items": rows})


@app.post("/api/stickies")
async def upsert_sticky(payload: dict[str, Any]) -> JSONResponse:
    sid = str(payload.get("id", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    import uuid
    if not sid:
        sid = uuid.uuid4().hex[:12]
        created_at = datetime.now(timezone.utc).isoformat()
    else:
        conn = _db(PRODUCTIVITY_DB)
        c = conn.cursor()
        c.execute("SELECT created_at FROM stickies WHERE id = ?", (sid,))
        row = c.fetchone()
        created_at = row[0] if row else datetime.now(timezone.utc).isoformat()
        conn.close()
    color = str(payload.get("color", "amber")).strip()
    position = float(payload.get("position", 0))
    updated_at = datetime.now(timezone.utc).isoformat()
    conn = _db(PRODUCTIVITY_DB)
    c = conn.cursor()
    c.execute(
        "INSERT INTO stickies (id, content, color, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET content=excluded.content, color=excluded.color, position=excluded.position, updated_at=excluded.updated_at",
        (sid, content, color, position, created_at, updated_at),
    )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "id": sid, "sticky": {"id": sid, "content": content, "color": color, "position": position, "created_at": created_at, "updated_at": updated_at}})


@app.delete("/api/stickies/{sid}")
async def delete_sticky(sid: str) -> JSONResponse:
    conn = _db(PRODUCTIVITY_DB)
    c = conn.cursor()
    c.execute("DELETE FROM stickies WHERE id = ?", (sid,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="sticky not found")
    return JSONResponse({"ok": True})


@app.get("/api/pomodoro-today")
async def get_pomodoro_today() -> JSONResponse:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = _db(PRODUCTIVITY_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT day, count, updated_at FROM pomodoro WHERE day = ?", (today,))
    row = c.fetchone()
    conn.close()
    if row:
        return JSONResponse({"day": row["day"], "count": row["count"], "updated_at": row["updated_at"]})
    return JSONResponse({"day": today, "count": 0, "updated_at": datetime.now(timezone.utc).isoformat()})


@app.post("/api/pomodoro-today/increment")
async def increment_pomodoro_today() -> JSONResponse:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    conn = _db(PRODUCTIVITY_DB, timeout=5)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "INSERT INTO pomodoro (day, count, updated_at) VALUES (?, ?, ?) ON CONFLICT(day) DO UPDATE SET count = count + 1, updated_at = excluded.updated_at RETURNING day, count, updated_at",
        (today, 1, now),
    )
    row = dict(c.fetchone())
    conn.commit()
    conn.close()
    return JSONResponse({"day": row["day"], "count": row["count"], "updated_at": row["updated_at"]})


# ===========================================================================
# Obsidian vault — direct filesystem access
# ---------------------------------------------------------------------------
# Read-only vault browser. We list .md files, return their rendered contents
# (with light markdown cleanup for the dashboard), and offer substring search
# across the vault. We never mutate files from the dashboard — Obsidian owns
# writes so plugin state stays consistent.
# ===========================================================================

# ===========================================================================
# Profile avatar — a single uploadable photo (replaces the "D" initial)
# ===========================================================================
PROFILE_AVATAR_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_AVATAR_BYTES = 8 * 1024 * 1024  # 8 MB is plenty for an avatar


def _current_avatar() -> Path | None:
    """The single stored avatar file (avatar.<ext>), or None."""
    for p in sorted(PROFILE_DIR.glob("avatar.*")):
        if p.is_file():
            return p
    return None


def _avatar_url(p: Path) -> str:
    try:
        mtime = int(p.stat().st_mtime)
    except OSError:
        mtime = 0
    return f"/static/profile/{p.name}?t={mtime}"   # cache-bust on every change


def _looks_like_image(b: bytes) -> bool:
    return (
        b[:8] == b"\x89PNG\r\n\x1a\n"                       # png
        or b[:3] == b"\xff\xd8\xff"                          # jpeg
        or b[:6] in (b"GIF87a", b"GIF89a")                   # gif
        or (b[:4] == b"RIFF" and b[8:12] == b"WEBP")         # webp
    )


@app.get("/api/profile")
async def get_profile() -> dict[str, Any]:
    """Return the current avatar URL (or null → the app bar shows the initial)."""
    p = _current_avatar()
    return {"avatar_url": _avatar_url(p) if p else None}


@app.post("/api/profile/avatar")
async def upload_profile_avatar(file: UploadFile = File(...)) -> dict[str, Any]:
    """Store a single profile photo, replacing any previous one. Only ever writes
    inside static/profile/; never touches anything else in the vault or home."""
    ext = Path(file.filename or "").suffix.lower()
    if ext == ".jpe":
        ext = ".jpg"
    if ext not in PROFILE_AVATAR_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported image type: {ext or '?'}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="image too large (max 8 MB)")
    if not _looks_like_image(data):
        raise HTTPException(status_code=400, detail="file is not a valid image")
    # One canonical avatar — drop any previous extension variants first.
    for old in PROFILE_DIR.glob("avatar.*"):
        try:
            old.unlink()
        except OSError:
            pass
    dest = PROFILE_DIR / f"avatar{ext}"
    async with aiofiles.open(dest, "wb") as fh:
        await fh.write(data)
    return {"ok": True, "avatar_url": _avatar_url(dest)}


@app.delete("/api/profile/avatar")
async def delete_profile_avatar() -> dict[str, Any]:
    """Remove the avatar → the app bar falls back to the initial."""
    removed = False
    for old in PROFILE_DIR.glob("avatar.*"):
        try:
            old.unlink()
            removed = True
        except OSError:
            pass
    return {"ok": True, "removed": removed}


# Aurora v2.0 additive tools router — FSRS spaced repetition, exam readiness,
# AI tutor. Self-contained module; existing routes are untouched.
from tools import router as tools_router  # noqa: E402
from tracker import router as tracker_router  # noqa: E402
from memory import router as memory_router  # noqa: E402
from notebooklm import router as notebooklm_router  # noqa: E402
from voice import router as voice_router  # noqa: E402
from stats import router as stats_router  # noqa: E402
from anki import router as anki_router  # noqa: E402
from terminal import router as terminal_router  # noqa: E402
from graph import router as graph_router  # noqa: E402
from tts import router as tts_router  # noqa: E402
from automation_hooks import router as automation_hooks_router  # noqa: E402
from system_health import router as system_health_router  # noqa: E402
from knowledge import router as knowledge_router  # noqa: E402
from orchestrator import router as orchestrator_router  # noqa: E402
from agents_api import router as agents_router  # noqa: E402
from agents_legacy_api import router as legacy_agents_router  # noqa: E402

app.include_router(tools_router)
app.include_router(tracker_router)
app.include_router(memory_router)
app.include_router(notebooklm_router)
app.include_router(agents_router)
app.include_router(legacy_agents_router)
app.include_router(obsidian_router)
app.include_router(search_router)
app.include_router(briefing_router)
app.include_router(practice_router)
app.include_router(voice_router)
app.include_router(stats_router)
app.include_router(anki_router)
app.include_router(terminal_router)
app.include_router(graph_router)
app.include_router(tts_router)
app.include_router(automation_hooks_router)
app.include_router(system_health_router)
app.include_router(knowledge_router)
app.include_router(orchestrator_router)

# Static frontend (mounted last so /api/* and / take precedence).
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=51763, log_level="info")
