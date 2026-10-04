"""
Anthropic Claude provider implementation.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.providers.base import (
    AuthenticationError,
    BaseProvider,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    ProviderConfig,
    ProviderError,
    RateLimitError,
)


class AnthropicProvider(BaseProvider):
    """Provider for Anthropic Claude API."""

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None):
        super().__init__(config)
        self._client = client
        self.base_url = (self.config.base_url or "https://api.anthropic.com/v1").rstrip("/")
        self.default_model = self.config.default_model or "claude-3-5-sonnet-20241022"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        if not self.is_configured:
            raise AuthenticationError("ANTHROPIC_API_KEY is not configured", provider=self.name)

        target_model = model or self.default_model
        endpoint = f"{self.base_url}/messages"

        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": "Personal-AI-System/1.1.0",
        }
        headers.update(self.config.headers)

        system_prompt = ""
        anthropic_messages = []
        for m in messages:
            if m.role == "system":
                system_prompt += f"{m.content}\n"
            else:
                anthropic_messages.append({"role": m.role, "content": m.content})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt.strip()
        payload.update(kwargs)

        client = self._get_client()
        start_time = time.perf_counter()
        try:
            resp = await client.post(endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Request timeout after {self.config.timeout}s: {exc}", provider=self.name) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Connection failed: {exc}", provider=self.name) from exc

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if resp.status_code == 401 or resp.status_code == 403:
            raise AuthenticationError(f"Anthropic authentication failed: {resp.text}", provider=self.name, status_code=resp.status_code)
        if resp.status_code == 429:
            raise RateLimitError(f"Anthropic rate limit reached: {resp.text}", provider=self.name, status_code=429)
        if resp.status_code >= 400:
            raise ProviderError(f"Anthropic returned error {resp.status_code}: {resp.text}", provider=self.name, status_code=resp.status_code)

        data = resp.json()
        content_blocks = data.get("content", [])
        text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
        finish_reason = data.get("stop_reason", "end_turn")

        raw_usage = data.get("usage", {})
        prompt_tokens = raw_usage.get("input_tokens", 0)
        completion_tokens = raw_usage.get("output_tokens", 0)
        usage = LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return LLMResponse(
            text=text,
            model=data.get("model", target_model),
            provider=self.name,
            usage=usage,
            latency_ms=round(latency_ms, 2),
            finish_reason=finish_reason,
            raw=data,
        )

    async def health_check(self) -> bool:
        if not self.is_configured:
            return False
        # Anthropic doesn't have a dedicated public GET /health, so verify key readiness
        return bool(self.config.api_key and len(self.config.api_key) > 10)
