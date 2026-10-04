"""Shared Hermes subprocess environment helpers."""

from __future__ import annotations

import os
from pathlib import Path

import config

AGENT_REQUIRED_KEYS = (
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


def inject_telegram_env(env: dict[str, str]) -> None:
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


def hydrate_agent_env(env: dict[str, str], profile_dir: Path) -> None:
    """Copy missing provider credentials from the agent profile env file."""
    env_file = profile_dir / ".env"
    if not env_file.is_file():
        env_file = config.ENV_FILE
    if not env_file.is_file() or all(env.get(key) for key in AGENT_REQUIRED_KEYS):
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
        if key in AGENT_REQUIRED_KEYS and not env.get(key):
            env[key] = value.strip().strip('"').strip("'")


def apply_agent_provider_env(env: dict[str, str]) -> tuple[str, str]:
    """Set provider/model variables used by dashboard-launched Hermes."""
    provider = os.environ.get("MC_AGENT_PROVIDER", DEFAULT_AGENT_PROVIDER).strip() or DEFAULT_AGENT_PROVIDER
    model = os.environ.get("MC_AGENT_MODEL", DEFAULT_AGENT_MODEL).strip() or DEFAULT_AGENT_MODEL
    env["HERMES_INFERENCE_PROVIDER"] = provider
    env["HERMES_INFERENCE_MODEL"] = model
    return provider, model


def build_agent_env(profile_dir: Path, *, hermes_home: Path | None = None, agent_log_db: Path | None = None) -> dict[str, str]:
    """Build a Hermes environment for a dashboard-launched agent."""
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home or profile_dir)
    if agent_log_db is not None:
        env["AGENT_LOG_DB"] = str(agent_log_db)
    inject_telegram_env(env)
    hydrate_agent_env(env, profile_dir)
    apply_agent_provider_env(env)
    return env
