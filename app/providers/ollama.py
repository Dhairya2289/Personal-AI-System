"""
Ollama provider for local offline LLM execution.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.providers.base import (
    BaseProvider,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    ProviderConfig,
    ProviderError,
)


class OllamaProvider(BaseProvider):
    """Provider for local Ollama server."""

    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None):
        super().__init__(config)
        self._client = client
        self.base_url = (self.config.base_url or "http://127.0.0.1:11434").rstrip("/")
        self.default_model = self.config.default_model or "llama3.2"

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout, connect=2.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
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
        endpoint = f"{self.base_url}/api/chat"

        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [m.to_dict() for m in messages],
            "stream": False,
            "options": options,
        }
        payload.update(kwargs)

        client = self._get_client()
        start_time = time.perf_counter()
        try:
            resp = await client.post(endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Ollama request timed out after {self.config.timeout}s", provider=self.name) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Could not connect to Ollama server at {self.base_url}: {exc}", provider=self.name) from exc

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if resp.status_code >= 400:
            raise ProviderError(f"Ollama returned error {resp.status_code}: {resp.text}", provider=self.name, status_code=resp.status_code)

        data = resp.json()
        msg = data.get("message", {})
        text = msg.get("content", "")

        prompt_eval = data.get("prompt_eval_count", 0)
        eval_count = data.get("eval_count", 0)
        usage = LLMUsage(
            prompt_tokens=prompt_eval,
            completion_tokens=eval_count,
            total_tokens=prompt_eval + eval_count,
        )

        return LLMResponse(
            text=text,
            model=data.get("model", target_model),
            provider=self.name,
            usage=usage,
            latency_ms=round(latency_ms, 2),
            finish_reason=data.get("done_reason", "stop"),
            raw=data,
        )

    async def health_check(self) -> bool:
        client = self._get_client()
        try:
            resp = await client.get(f"{self.base_url}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False
