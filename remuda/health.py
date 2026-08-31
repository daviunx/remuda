"""Probing a pool's members on demand (FR-12).

Free-model availability shifts week to week. This answers "which of my pool
actually answers today" for the cost of one minimal request per member — and
it reports, it does not judge: a dead member is a row in the table, not a
non-zero exit.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from remuda.catalog.resolve import ResolvedPool, resolve_pool, to_model_configs
from remuda.registry.models import ModelConfig
from remuda.registry.registry import Registry
from remuda.transport.errors import TransportError
from remuda.transport.models import CompletionRequest
from remuda.transport.openai_compat import build_request_extras, build_transport

#: The smallest thing worth asking a model, kept identical across members.
PROBE_PROMPT = "Reply with the single word: OK"

PROBE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class MemberHealth:
    """One pool member's answer to the probe."""

    name: str
    provider: str
    model_id: str
    is_alive: bool
    latency_ms: float = 0.0
    detail: str = ""

    @property
    def answer(self) -> str:
        """The model's reply, when it gave one."""
        return self.detail if self.is_alive else ""


async def probe_pool(
    pool_name: str,
    registry: Registry,
    client: httpx.AsyncClient,
) -> tuple[ResolvedPool, list[MemberHealth]]:
    """Send one minimal request to every member of a pool.

    Writes nothing: no run directory, no ledger, no report.

    Raises:
        RegistryError: the pool is not registered.
        CatalogError: a `discover:` member could not be resolved.
    """
    resolved = await resolve_pool(registry.pool(pool_name), registry, client)
    results = [
        await _probe_one(model, registry) for model in to_model_configs(resolved)
    ]
    return resolved, results


async def _probe_one(model: ModelConfig, registry: Registry) -> MemberHealth:
    provider = registry.provider(model.provider)
    transport = build_transport(provider)
    started = time.monotonic()
    try:
        result = await transport.complete(
            CompletionRequest(
                model_id=model.model_id,
                prompt=PROBE_PROMPT,
                extra_body=build_request_extras(model),
                timeout_seconds=PROBE_TIMEOUT_SECONDS,
            )
        )
    except TransportError as error:
        return MemberHealth(
            name=model.name,
            provider=provider.name,
            model_id=model.model_id,
            is_alive=False,
            latency_ms=(time.monotonic() - started) * 1000,
            detail=str(error),
        )
    finally:
        await transport.aclose()

    return MemberHealth(
        name=model.name,
        provider=provider.name,
        model_id=model.model_id,
        is_alive=True,
        latency_ms=result.latency_ms,
        detail=" ".join(result.text.split())[:60],
    )


def alive_count(results: Sequence[MemberHealth]) -> int:
    """How many members answered."""
    return sum(1 for result in results if result.is_alive)
