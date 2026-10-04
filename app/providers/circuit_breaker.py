"""
Circuit breaker implementation for protecting against cascading LLM outages.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any


class CircuitBreaker:
    """
    Stateful circuit breaker tracking failure rates per provider.
    Transitions:
      CLOSED (healthy) -> OPEN (after threshold failures) -> HALF-OPEN (after cooldown) -> CLOSED / OPEN
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._last_failure: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def record_success(self, provider_name: str) -> None:
        """Reset failure counts on successful call."""
        async with self._lock:
            self._failures.pop(provider_name, None)
            self._last_failure.pop(provider_name, None)

    async def record_failure(self, provider_name: str) -> None:
        """Increment failure counter and record timestamp."""
        async with self._lock:
            self._failures[provider_name] = self._failures.get(provider_name, 0) + 1
            self._last_failure[provider_name] = time.time()

    async def is_open(self, provider_name: str) -> bool:
        """
        Check if the circuit is tripped.
        Returns True if calls should be blocked, False if permitted.
        Transitions to half-open when cooldown expires to allow a single probe.
        """
        async with self._lock:
            fails = self._failures.get(provider_name, 0)
            if fails < self.failure_threshold:
                return False

            last = self._last_failure.get(provider_name, 0.0)
            if time.time() - last > self.cooldown_seconds:
                # Cooldown expired — enter half-open state, allow probe attempt
                self._failures[provider_name] = self.failure_threshold - 1
                return False

            return True

    async def get_status(self) -> dict[str, dict[str, Any]]:
        """Return diagnostic view of all tracked providers."""
        async with self._lock:
            now = time.time()
            status = {}
            for name, count in self._failures.items():
                last = self._last_failure.get(name, 0.0)
                is_open = count >= self.failure_threshold and (now - last <= self.cooldown_seconds)
                status[name] = {
                    "consecutive_failures": count,
                    "last_failure_ago_sec": round(now - last, 1) if last else None,
                    "state": "OPEN" if is_open else ("HALF_OPEN" if count >= self.failure_threshold else "CLOSED"),
                }
            return status
