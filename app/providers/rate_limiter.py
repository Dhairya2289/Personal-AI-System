"""
Sliding-window rate limiter for LLM provider quotas (RPM / RPD).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any


class RateLimiter:
    """Tracks RPM and RPD to avoid provider 429 rate limit errors."""

    def __init__(self):
        self._counts: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def record_call(self, provider_name: str) -> None:
        """Record an API invocation for the specified provider."""
        async with self._lock:
            now = time.time()
            c = self._counts.setdefault(
                provider_name,
                {"rpm": 0.0, "rpd": 0.0, "rpm_reset": now, "rpd_reset": now},
            )
            # 60-second window reset
            if now - c["rpm_reset"] >= 60.0:
                c["rpm"] = 0.0
                c["rpm_reset"] = now
            # 24-hour window reset
            if now - c["rpd_reset"] >= 86400.0:
                c["rpd"] = 0.0
                c["rpd_reset"] = now

            c["rpm"] += 1.0
            c["rpd"] += 1.0

    async def is_available(
        self, provider_name: str, rpm_limit: int = 60, rpd_limit: int = 10000
    ) -> bool:
        """Check whether provider quota allows another call."""
        async with self._lock:
            now = time.time()
            c = self._counts.get(provider_name)
            if not c:
                return True

            rpm_ok = (now - c.get("rpm_reset", 0.0) >= 60.0) or (c.get("rpm", 0.0) < rpm_limit)
            rpd_ok = (now - c.get("rpd_reset", 0.0) >= 86400.0) or (c.get("rpd", 0.0) < rpd_limit)
            return rpm_ok and rpd_ok

    async def get_usage(self, provider_name: str) -> dict[str, Any]:
        """Return usage telemetry for diagnostics."""
        async with self._lock:
            now = time.time()
            c = self._counts.get(provider_name, {})
            return {
                "rpm": int(c.get("rpm", 0)) if (now - c.get("rpm_reset", 0) < 60.0) else 0,
                "rpd": int(c.get("rpd", 0)) if (now - c.get("rpd_reset", 0) < 86400.0) else 0,
            }
