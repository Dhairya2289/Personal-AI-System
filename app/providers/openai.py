"""
OpenAI-compatible LLM provider implementation.
Covers OpenAI, Groq, OpenRouter, Sambanova, DeepSeek, and local vLLM instances.
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


class OpenAIProvider(BaseProvider):
    """Provider for OpenAI-compatible REST APIs."""

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None):
        super().__init__(config)
        self._client = client
        self.base_url = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        self.default_model = self.config.default_model or "gpt-4o-mini"

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
        target_model = model or self.default_model
        endpoint = f"{self.base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Personal-AI-System/1.1.0",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers.update(self.config.headers)

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
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
            raise AuthenticationError(f"Authentication failed: {resp.text}", provider=self.name, status_code=resp.status_code)
        if resp.status_code == 429:
            raise RateLimitError(f"Rate limited by {self.name}: {resp.text}", provider=self.name, status_code=429)
        if resp.status_code >= 400:
            raise ProviderError(f"API returned error {resp.status_code}: {resp.text}", provider=self.name, status_code=resp.status_code)

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        text = msg.get("content") or ""
        finish_reason = choice.get("finish_reason", "stop")

        raw_usage = data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
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
        client = self._get_client()
        endpoint = f"{self.base_url}/models"
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            resp = await client.get(endpoint, headers=headers, timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False
