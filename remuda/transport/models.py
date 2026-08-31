"""The transport contract: one request in, one answer out."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CompletionRequest:
    """One completion to perform against one model."""

    model_id: str
    prompt: str
    system: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class Usage:
    """What the call consumed, as far as the provider reports it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion tokens."""
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class CompletionResult:
    """The raw answer, plus what it cost to get it."""

    text: str
    model_id: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0


@runtime_checkable
class Transport(Protocol):
    """How remuda reaches a model. Implementations must be concurrency-safe.

    Raises (from `complete`):
        TransientTransportError: cool the model down, redistribute its work.
        PermanentTransportError: remove the model from the run.
    """

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Perform one completion."""
        ...

    async def aclose(self) -> None:
        """Release any held resources."""
        ...
