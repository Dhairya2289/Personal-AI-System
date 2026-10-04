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
from app.runtime.hermes import build_agent_env
from app.runtime.process import communicate_with_timeout

router = APIRouter(prefix="/api/agents", tags=["legacy-agents"])

HERMES_PYTHON = config.HERMES_PYTHON
HERMES_HOME_BILL = config.HERMES_HOME
PROFILES_DIR = config.PROFILES_DIR
AGENT_LOG_DB = config.AGENT_LOG_DB

KNOWN_AGENTS = {"bill", "vault", "scholar", "quizmaster", "planner", "dev"}


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

    env = build_agent_env(
        hermes_home,
        hermes_home=hermes_home,
        agent_log_db=AGENT_LOG_DB,
    )
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
    stdout_bytes, stderr_bytes, timed_out = await communicate_with_timeout(proc, timeout)

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
