"""Legacy Hermes one-shot agent API used by the existing dashboard.

This module owns the subprocess integration so the main FastAPI application
does not also have to own Hermes-specific environment and process details.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import config

router = APIRouter(prefix="/api/agents", tags=["legacy-agents"])

HERMES_PYTHON = config.HERMES_PYTHON
HERMES_HOME_BILL = config.HERMES_HOME
PROFILES_DIR = config.PROFILES_DIR
AGENT_LOG_DB = config.AGENT_LOG_DB

KNOWN_AGENTS = {"bill", "vault", "scholar", "quizmaster", "planner", "dev"}

_AGENT_REQUIRED_KEYS = (
    "TOKENROUTER_API_KEY",
    "TOKENROUTER_BASE_URL",
    "TOKENROUTER_MODEL",
    "TOKENLB_API_KEY",
    "TOKENLB_BASE_URL",
    "NOUS_API_KEY",
    "KIRO_GATEWAY_API_KEY",
    "KIMCHI_API_KEY",
    "KIMCHI_BASE_URL",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)

DEFAULT_AGENT_PROVIDER = os.environ.get("MC_AGENT_PROVIDER", "custom:omniroute")
DEFAULT_AGENT_MODEL = os.environ.get("MC_AGENT_MODEL", "kimchi/kimi-k2.7")


def _inject_telegram_env(env: dict[str, str]) -> None:
    """Hydrate Telegram settings from the local Hermes env file when needed."""
    env_file = config.ENV_FILE
    if not env_file.is_file() or all(
        env.get(key) for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL")
    ):
        return

    try:
        text = env_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    needed = {"TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL"}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in needed and not env.get(key):
            env[key] = value.strip()


def _hydrate_agent_env(env: dict[str, str], profile_dir: Path) -> None:
    """Copy missing provider credentials from the agent profile env file."""
    env_file = profile_dir / ".env"
    if not env_file.is_file():
        env_file = config.ENV_FILE
    if not env_file.is_file() or all(env.get(key) for key in _AGENT_REQUIRED_KEYS):
        return

    try:
        text = env_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in _AGENT_REQUIRED_KEYS and not env.get(key):
            env[key] = value.strip().strip('"').strip("'")


def _apply_agent_provider_env(env: dict[str, str]) -> tuple[str, str]:
    provider = os.environ.get("MC_AGENT_PROVIDER", DEFAULT_AGENT_PROVIDER).strip()
    model = os.environ.get("MC_AGENT_MODEL", DEFAULT_AGENT_MODEL).strip()
    provider = provider or DEFAULT_AGENT_PROVIDER
    model = model or DEFAULT_AGENT_MODEL
    env["HERMES_INFERENCE_PROVIDER"] = provider
    env["HERMES_INFERENCE_MODEL"] = model
    return provider, model


def _signal_process_group(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _communicate_with_timeout(
    proc: asyncio.subprocess.Process,
    timeout: float | None,
) -> tuple[bytes, bytes, bool]:
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout_bytes, stderr_bytes, False
    except asyncio.TimeoutError:
        _signal_process_group(proc, signal.SIGTERM)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        except asyncio.TimeoutError:
            _signal_process_group(proc, signal.SIGKILL)
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        return stdout_bytes, stderr_bytes, True


async def run_agent_oneshot(
    agent: str,
    task: str,
    *,
    timeout: float | None = 600.0,
) -> dict[str, Any]:
    """Launch one Hermes process for an agent and execute a task."""
    agent = agent.strip().lower()
    if agent not in KNOWN_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown agent '{agent}'. expected one of: {sorted(KNOWN_AGENTS)}",
        )
    if not task or not task.strip():
        raise HTTPException(status_code=400, detail="task text is required")

    hermes_home = HERMES_HOME_BILL if agent == "bill" else PROFILES_DIR / agent
    if not hermes_home.is_dir():
        raise HTTPException(
            status_code=500,
            detail=f"HERMES_HOME for '{agent}' does not exist: {hermes_home}",
        )
    if not HERMES_PYTHON.is_file():
        raise HTTPException(status_code=500, detail=f"hermes python not found: {HERMES_PYTHON}")

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["AGENT_LOG_DB"] = str(AGENT_LOG_DB)
    _inject_telegram_env(env)
    _hydrate_agent_env(env, hermes_home)
    _apply_agent_provider_env(env)

    cmd = [
        str(HERMES_PYTHON),
        "-m",
        "hermes_cli.main",
        "-z",
        task,
        "--yolo",
        "--accept-hooks",
    ]

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes, timed_out = await _communicate_with_timeout(proc, timeout)

    return {
        "agent": agent,
        "task": task,
        "returncode": proc.returncode,
        "duration_s": round(time.monotonic() - started, 3),
        "timed_out": timed_out,
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        "env": {
            "HERMES_HOME": env["HERMES_HOME"],
            "AGENT_LOG_DB": env["AGENT_LOG_DB"],
        },
        "cmd": cmd,
    }


@router.post("/run")
async def run_agent(payload: dict[str, Any]) -> JSONResponse:
    """Trigger a one-shot legacy Hermes agent run."""
    agent = str(payload.get("agent", "")).strip().lower()
    task = str(payload.get("task", "")).strip()
    timeout_raw = payload.get("timeout", 600)

    try:
        timeout = float(timeout_raw) if timeout_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout must be a number")

    result = await run_agent_oneshot(agent, task, timeout=timeout)
    return JSONResponse(result)
