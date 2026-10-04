"""
Unit tests for the Provider Manager, Circuit Breaker, Rate Limiter, and Cache.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.providers.base import (
    BaseProvider,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    ProviderConfig,
    ProviderError,
)
from app.providers.cache import ResponseCache
from app.providers.circuit_breaker import CircuitBreaker
from app.providers.manager import ProviderManager
from app.providers.rate_limiter import RateLimiter


class MockSuccessProvider(BaseProvider):
    def __init__(self, name: str = "mock-success"):
        super().__init__(ProviderConfig(name=name, api_key="secret", default_model="mock-v1"))

    async def generate(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        return LLMResponse(
            text=f"Response from {self.name}",
            model="mock-v1",
            provider=self.name,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            latency_ms=12.5,
        )

    async def health_check(self) -> bool:
        return True


class MockFailingProvider(BaseProvider):
    def __init__(self, name: str = "mock-fail", error_cls=ProviderError):
        super().__init__(ProviderConfig(name=name, api_key="secret", max_retries=1))
        self.error_cls = error_cls

    async def generate(self, messages: list[LLMMessage], **kwargs) -> LLMResponse:
        raise self.error_cls(f"Intentional failure from {self.name}", provider=self.name)

    async def health_check(self) -> bool:
        return False


# ── CircuitBreaker Tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_transitions():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.1)

    # Initial state is closed
    assert not await breaker.is_open("openai")

    # Record first failure: still closed
    await breaker.record_failure("openai")
    assert not await breaker.is_open("openai")

    # Record second failure: tripped (OPEN)
    await breaker.record_failure("openai")
    assert await breaker.is_open("openai")

    # Cooldown expires: should become half-open (allows one probe)
    await asyncio.sleep(0.12)
    assert not await breaker.is_open("openai")

    # Record success: closes circuit
    await breaker.record_success("openai")
    assert not await breaker.is_open("openai")


# ── RateLimiter Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter():
    limiter = RateLimiter()
    provider = "gemini"

    # Initially available
    assert await limiter.is_available(provider, rpm_limit=2)

    await limiter.record_call(provider)
    assert await limiter.is_available(provider, rpm_limit=2)

    await limiter.record_call(provider)
    # Reached limit (2 calls)
    assert not await limiter.is_available(provider, rpm_limit=2)

    usage = await limiter.get_usage(provider)
    assert usage["rpm"] == 2


# ── ResponseCache Tests ───────────────────────────────────


def test_response_cache(tmp_path: Path):
    db_file = tmp_path / "test_cache.db"
    cache = ResponseCache(db_path=db_file)

    payload = {"text": "Hello, Dhairya!", "model": "gpt-4o", "provider": "openai"}
    cache.set("general", "gpt-4o", "Hello!", payload, ttl_seconds=10.0)

    cached = cache.get("general", "gpt-4o", "Hello!")
    assert cached is not None
    assert cached["text"] == "Hello, Dhairya!"

    # Cache miss
    assert cache.get("general", "gpt-4o", "Unknown prompt") is None


# ── ProviderManager Tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_provider_manager_success(tmp_path: Path):
    cache = ResponseCache(db_path=tmp_path / "cache.db")
    mgr = ProviderManager(cache=cache)
    mgr.providers.clear()

    provider = MockSuccessProvider("test-provider")
    mgr.register_provider(provider)

    resp = await mgr.generate("Hello!", provider_name="test-provider")
    assert resp.text == "Response from test-provider"
    assert resp.provider == "test-provider"


@pytest.mark.asyncio
async def test_provider_manager_fallback(tmp_path: Path):
    cache = ResponseCache(db_path=tmp_path / "cache.db")
    mgr = ProviderManager(cache=cache)
    mgr.providers.clear()

    # Register failing provider as primary and healthy provider as secondary
    failing = MockFailingProvider("primary-fail")
    backup = MockSuccessProvider("backup-success")

    mgr.register_provider(failing)
    mgr.register_provider(backup)
    mgr.task_routing["test_task"] = ["primary-fail", "backup-success"]

    resp = await mgr.generate("Perform task", task_type="test_task")
    assert resp.text == "Response from backup-success"
    assert resp.provider == "backup-success"
