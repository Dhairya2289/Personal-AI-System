"""
Central Provider Manager orchestrating model routing, circuit breaking,
rate-limiting, retries, response caching, and fallback chains.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    AuthenticationError,
    BaseProvider,
    LLMMessage,
    LLMResponse,
    ProviderConfig,
    ProviderError,
    RateLimitError,
)
from app.providers.cache import ResponseCache
from app.providers.circuit_breaker import CircuitBreaker
from app.providers.gemini import GeminiProvider
from app.providers.ollama import OllamaProvider
from app.providers.openai import OpenAIProvider
from app.providers.rate_limiter import RateLimiter

logger = logging.getLogger("personal_ai.providers")

DEFAULT_TASK_ROUTING: dict[str, list[str]] = {
    "speed": ["groq", "ollama", "gemini", "openai"],
    "code": ["openai", "anthropic", "gemini", "groq"],
    "logic": ["anthropic", "openai", "gemini"],
    "research": ["gemini", "anthropic", "openai"],
    "local": ["ollama"],
    "general": ["gemini", "openai", "anthropic", "groq", "ollama"],
}


class ProviderManager:
    """Enterprise-grade LLM provider orchestrator with circuit breakers and fallback."""

    def __init__(self, cache: ResponseCache | None = None):
        self.providers: dict[str, BaseProvider] = {}
        self.breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
        self.limiter = RateLimiter()
        self.cache = cache or ResponseCache()
        self.task_routing = dict(DEFAULT_TASK_ROUTING)
        self._init_default_providers()

    def _init_default_providers(self) -> None:
        """Initialize known providers from environment / configuration."""
        # 1. Google Gemini
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.register_provider(
            GeminiProvider(
                ProviderConfig(
                    name="gemini",
                    api_key=gemini_key,
                    default_model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                    timeout=45.0,
                    rpm_limit=int(os.environ.get("GEMINI_RPM", "15")),
                )
            )
        )

        # 2. OpenAI
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        self.register_provider(
            OpenAIProvider(
                ProviderConfig(
                    name="openai",
                    api_key=openai_key,
                    default_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                    timeout=30.0,
                    rpm_limit=int(os.environ.get("OPENAI_RPM", "60")),
                )
            )
        )

        # 3. Anthropic Claude
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self.register_provider(
            AnthropicProvider(
                ProviderConfig(
                    name="anthropic",
                    api_key=anthropic_key,
                    default_model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                    timeout=45.0,
                    rpm_limit=int(os.environ.get("ANTHROPIC_RPM", "50")),
                )
            )
        )

        # 4. Groq (Fast OpenAI-compatible)
        groq_key = os.environ.get("GROQ_API_KEY", "")
        self.register_provider(
            OpenAIProvider(
                ProviderConfig(
                    name="groq",
                    api_key=groq_key,
                    base_url="https://api.groq.com/openai/v1",
                    default_model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    timeout=20.0,
                    rpm_limit=int(os.environ.get("GROQ_RPM", "30")),
                )
            )
        )

        # 5. OpenRouter
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        self.register_provider(
            OpenAIProvider(
                ProviderConfig(
                    name="openrouter",
                    api_key=openrouter_key,
                    base_url="https://openrouter.ai/api/v1",
                    default_model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-haiku"),
                    timeout=45.0,
                    headers={"HTTP-Referer": "https://github.com/Dhairya2289/Personal-AI-System"},
                )
            )
        )

        # 6. Local Ollama
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        self.register_provider(
            OllamaProvider(
                ProviderConfig(
                    name="ollama",
                    base_url=ollama_url,
                    default_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
                    timeout=60.0,
                )
            )
        )

    def register_provider(self, provider: BaseProvider) -> None:
        """Register or override a provider implementation."""
        self.providers[provider.name] = provider

    def get_provider(self, name: str) -> BaseProvider | None:
        return self.providers.get(name)

    async def generate(
        self,
        prompt_or_messages: str | list[LLMMessage],
        *,
        task_type: str = "general",
        provider_name: str | None = None,
        model: str | None = None,
        use_cache: bool = True,
        cache_ttl: float = 3600.0,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Execute completion with caching, circuit breaking, retries, and fallback.
        """
        # Convert raw string to message list
        if isinstance(prompt_or_messages, str):
            messages = [LLMMessage(role="user", content=prompt_or_messages)]
            cache_prompt = prompt_or_messages
        else:
            messages = prompt_or_messages
            cache_prompt = "\n".join(f"{m.role}:{m.content}" for m in messages)

        # 1. Check cache
        target_model = model or "default"
        if use_cache:
            cached = self.cache.get(task_type, target_model, cache_prompt)
            if cached:
                logger.info(f"LLM cache hit for task_type={task_type}")
                return LLMResponse(
                    text=cached["text"],
                    model=cached["model"],
                    provider=cached["provider"] + " (cached)",
                    latency_ms=0.0,
                    finish_reason="cache_hit",
                )

        # 2. Determine provider candidates
        if provider_name:
            candidates = [provider_name]
        else:
            candidates = self.task_routing.get(task_type, self.task_routing["general"])

        last_error: Exception | None = None

        for candidate_name in candidates:
            provider = self.providers.get(candidate_name)
            if not provider or not provider.is_configured:
                continue

            # Check circuit breaker
            if await self.breaker.is_open(candidate_name):
                logger.warning(f"Circuit breaker is OPEN for provider '{candidate_name}'. Skipping.")
                continue

            # Check rate limiter
            if not await self.limiter.is_available(
                candidate_name, provider.config.rpm_limit, provider.config.rpd_limit
            ):
                logger.warning(f"Rate limit exceeded for provider '{candidate_name}'. Skipping.")
                continue

            # Attempt provider execution with backoff retry
            max_retries = provider.config.max_retries
            for attempt in range(max_retries + 1):
                try:
                    await self.limiter.record_call(candidate_name)
                    response = await provider.generate(
                        messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    # Success: record in circuit breaker and cache
                    await self.breaker.record_success(candidate_name)
                    if use_cache and response.text:
                        self.cache.set(
                            task_type,
                            response.model,
                            cache_prompt,
                            {
                                "text": response.text,
                                "model": response.model,
                                "provider": response.provider,
                            },
                            ttl_seconds=cache_ttl,
                        )
                    return response

                except (RateLimitError, AuthenticationError) as exc:
                    logger.error(f"Provider {candidate_name} fatal error: {exc}")
                    await self.breaker.record_failure(candidate_name)
                    last_error = exc
                    break  # Break retry loop, try next provider fallback

                except Exception as exc:
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries + 1} failed for {candidate_name}: {exc}"
                    )
                    last_error = exc
                    if attempt < max_retries:
                        await asyncio.sleep(1.0 * (2**attempt))
                    else:
                        await self.breaker.record_failure(candidate_name)

        # If all candidates exhausted
        raise ProviderError(
            f"All providers exhausted for task '{task_type}'. Last error: {last_error}"
        ) from last_error

    async def doctor(self) -> dict[str, dict[str, Any]]:
        """Run health and diagnostics across all registered providers."""
        results: dict[str, dict[str, Any]] = {}
        breaker_status = await self.breaker.get_status()

        for name, provider in self.providers.items():
            is_healthy = False
            error_msg = None
            if provider.is_configured:
                try:
                    is_healthy = await provider.health_check()
                except Exception as exc:
                    error_msg = str(exc)

            usage = await self.limiter.get_usage(name)
            cb = breaker_status.get(name, {"state": "CLOSED", "consecutive_failures": 0})

            results[name] = {
                "configured": provider.is_configured,
                "healthy": is_healthy,
                "model": provider.config.default_model,
                "circuit_state": cb["state"],
                "recent_failures": cb["consecutive_failures"],
                "rpm_used": usage["rpm"],
                "rpm_limit": provider.config.rpm_limit,
                "error": error_msg,
            }
        return results


_default_manager: ProviderManager | None = None


def get_provider_manager() -> ProviderManager:
    """Return the global ProviderManager singleton."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ProviderManager()
    return _default_manager
