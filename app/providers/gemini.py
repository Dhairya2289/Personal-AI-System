"""
Google Gemini provider implementation using direct REST endpoints.
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


class GeminiProvider(BaseProvider):
    """Provider for Google Gemini API."""

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None):
        super().__init__(config)
        self._client = client
        self.base_url = (self.config.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        self.default_model = self.config.default_model or "gemini-2.0-flash"

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
            raise AuthenticationError("GEMINI_API_KEY is not configured", provider=self.name)

        target_model = model or self.default_model
        endpoint = f"{self.base_url}/models/{target_model}:generateContent?key={self.config.api_key}"

        system_instruction = None
        gemini_contents = []

        for m in messages:
            if m.role == "system":
                system_instruction = {"parts": [{"text": m.content}]}
            else:
                role = "user" if m.role == "user" else "model"
                gemini_contents.append({
                    "role": role,
                    "parts": [{"text": m.content}],
                })

        generation_config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens

        payload: dict[str, Any] = {
            "contents": gemini_contents,
            "generationConfig": generation_config,
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        payload.update(kwargs)

        client = self._get_client()
        start_time = time.perf_counter()
        try:
            resp = await client.post(endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Request timeout after {self.config.timeout}s: {exc}", provider=self.name) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Connection failed: {exc}", provider=self.name) from exc

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if resp.status_code == 400 and "API_KEY_INVALID" in resp.text:
            raise AuthenticationError(f"Invalid Gemini API key: {resp.text}", provider=self.name, status_code=400)
        if resp.status_code == 429:
            raise RateLimitError(f"Rate limited by Gemini: {resp.text}", provider=self.name, status_code=429)
        if resp.status_code >= 400:
            raise ProviderError(f"Gemini API returned error {resp.status_code}: {resp.text}", provider=self.name, status_code=resp.status_code)

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            text = ""
            finish_reason = "empty"
        else:
            cand = candidates[0]
            parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            finish_reason = cand.get("finishReason", "STOP").lower()

        usage_meta = data.get("usageMetadata", {})
        usage = LLMUsage(
            prompt_tokens=usage_meta.get("promptTokenCount", 0),
            completion_tokens=usage_meta.get("candidatesTokenCount", 0),
            total_tokens=usage_meta.get("totalTokenCount", 0),
        )

        return LLMResponse(
            text=text,
            model=target_model,
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
        endpoint = f"{self.base_url}/models?key={self.config.api_key}"
        try:
            resp = await client.get(endpoint, timeout=5.0)
            return resp.status_code < 500
        except Exception:
            return False
