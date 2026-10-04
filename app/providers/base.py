"""
Base abstractions, data models, and exceptions for AI providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Base exception for provider failures."""
    def __init__(self, message: str, provider: str = "", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class RateLimitError(ProviderError):
    """Raised when a provider hits rate limits (RPM or RPD)."""


class CircuitBreakerOpenError(ProviderError):
    """Raised when calls are blocked by an open circuit breaker."""


class AuthenticationError(ProviderError):
    """Raised when API credentials are missing or rejected."""


@dataclass
class LLMMessage:
    """A single chat message exchanged with an LLM."""
    role: str  # 'system', 'user', 'assistant'
    content: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class LLMUsage:
    """Token consumption statistics for a request."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Standardized response from an LLM provider."""
    text: str
    model: str
    provider: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderConfig:
    """Configuration options for a single LLM provider."""
    name: str
    api_key: str = ""
    base_url: str = ""
    default_model: str = ""
    timeout: float = 30.0
    max_retries: int = 2
    rpm_limit: int = 60
    rpd_limit: int = 10000
    headers: dict[str, str] = field(default_factory=dict)


class BaseProvider(ABC):
    """Abstract interface that all LLM providers must implement."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def is_configured(self) -> bool:
        """True if the provider has the necessary credentials or runs locally."""
        if self.name == "ollama":
            return True
        return bool(self.config.api_key)

    @abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a chat response from the provider."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Perform a quick probe to verify provider availability."""
        raise NotImplementedError
