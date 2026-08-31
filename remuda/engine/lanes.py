"""Per-model lanes: concurrency, request rate, cooldowns and ceilings (FR-4).

One lane per registered model. The lane is where a model's ceilings live, so
the ladder above it can stay a pure state machine: it asks whether a lane is
available and calls it, never how the model is throttled.
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from remuda.errors import RemudaError
from remuda.ledger.models import ModelStats
from remuda.registry.models import ModelConfig
from remuda.transport.models import CompletionRequest, CompletionResult, Transport

#: Cooldown after consecutive transient failures, in seconds.
_COOLDOWN_BASE = 2.0
_COOLDOWN_CEILING = 60.0

_RATE_WINDOW_SECONDS = 60.0


class LaneUnavailableError(RemudaError):
    """The lane is cooling down, disabled, or over its ceiling."""


class ModelLane:
    """One model's call path, with its ceilings and its running stats."""

    def __init__(
        self,
        model: ModelConfig,
        transport: Transport,
        extra_body: Mapping[str, Any] | None = None,
        timeout_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self.stats = ModelStats()
        self._transport = transport
        self._extra_body = dict(extra_body or {})
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._semaphore = asyncio.Semaphore(model.concurrency)
        self._recent_calls: deque[float] = deque()
        self._cooldown_until = 0.0
        self._consecutive_transients = 0
        self._disabled_reason: str | None = None

    @property
    def name(self) -> str:
        """The registry name of this lane's model."""
        return self.model.name

    @property
    def disabled_reason(self) -> str | None:
        """Why the lane was removed from the run, if it was."""
        return self._disabled_reason

    @property
    def is_cooling(self) -> bool:
        """True while the lane is serving out a transient-failure cooldown."""
        return self._clock() < self._cooldown_until

    @property
    def is_available(self) -> bool:
        """True when the ladder may route work to this lane right now."""
        return self._disabled_reason is None and not self.is_cooling

    def disable(self, reason: str) -> None:
        """Remove the lane from the run — never the run itself (FR-4)."""
        self._disabled_reason = reason

    def cool_down(self, seconds: float | None = None) -> float:
        """Cool the lane down after a transient failure. Returns the delay."""
        self._consecutive_transients += 1
        delay = (
            seconds
            if seconds is not None
            else min(
                _COOLDOWN_BASE * (2 ** (self._consecutive_transients - 1)),
                _COOLDOWN_CEILING,
            )
        )
        self._cooldown_until = self._clock() + delay
        self.stats.transient_failures += 1
        return delay

    def record_reject(self, count: int = 1) -> None:
        """Count answers this model produced that failed validation."""
        self.stats.rejects += count

    def record_answers(self, count: int) -> None:
        """Count answers this model produced that passed validation."""
        self.stats.answers += count

    async def call(self, prompt: str, system: str | None = None) -> CompletionResult:
        """Perform one completion under this lane's ceilings.

        Raises:
            LaneUnavailableError: the lane is disabled.
            TransientTransportError / PermanentTransportError: from the
                transport, unchanged — the ladder classifies them.
        """
        if self._disabled_reason is not None:
            raise LaneUnavailableError(
                f"model '{self.name}' is out of this run: {self._disabled_reason}"
            )
        async with self._semaphore:
            await self._await_rate_slot()
            result = await self._transport.complete(
                CompletionRequest(
                    model_id=self.model.model_id,
                    prompt=prompt,
                    system=system,
                    extra_body=self._extra_body,
                    timeout_seconds=self._timeout_seconds,
                )
            )
        self._record_success(result)
        return result

    def _record_success(self, result: CompletionResult) -> None:
        self._consecutive_transients = 0
        self.stats.calls += 1
        self.stats.latency_ms_total += result.latency_ms
        self.stats.prompt_tokens += result.usage.prompt_tokens
        self.stats.completion_tokens += result.usage.completion_tokens
        cost = result.usage.cost_usd
        if cost is None:
            # The provider reported no cost — derive it from the catalog
            # pricing carried on the model, when there is any.
            cost = self.model.cost_of(
                result.usage.prompt_tokens, result.usage.completion_tokens
            )
        if cost is not None:
            self.stats.cost_usd += cost
        ceiling = self.model.max_cost_usd
        if ceiling is not None and self.stats.cost_usd >= ceiling:
            self.disable(
                f"spend ceiling of ${ceiling:.4f} reached "
                f"(${self.stats.cost_usd:.4f} spent)"
            )

    async def _await_rate_slot(self) -> None:
        rpm = self.model.rpm
        if rpm is None:
            return
        while True:
            now = self._clock()
            while self._recent_calls and now - self._recent_calls[0] >= (
                _RATE_WINDOW_SECONDS
            ):
                self._recent_calls.popleft()
            if len(self._recent_calls) < rpm:
                self._recent_calls.append(now)
                return
            await asyncio.sleep(
                max(0.01, _RATE_WINDOW_SECONDS - (now - self._recent_calls[0]))
            )
