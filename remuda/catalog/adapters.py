"""One discovery adapter per provider shape (FR-3).

Every adapter answers two questions: what models does this provider serve,
and which filters can I answer about them. The second is what keeps an
unanswerable filter a refusal instead of a silent no-op.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from remuda.catalog.errors import CatalogError
from remuda.catalog.models import (
    FULL_CAPABILITIES,
    NAME_ONLY_CAPABILITIES,
    CatalogModel,
)
from remuda.registry.models import Provider

OPENROUTER = "openrouter"
OPENAI_COMPAT = "openai_compat"
OLLAMA = "ollama"


@runtime_checkable
class CatalogAdapter(Protocol):
    """Reads a provider's model list."""

    kind: str
    capabilities: frozenset[str]

    async def fetch(
        self, provider: Provider, client: httpx.AsyncClient
    ) -> list[CatalogModel]:
        """Return every model the provider currently serves."""
        ...


class OpenRouterCatalog:
    """`GET /api/v1/models` — pricing, context length and throughput."""

    kind = OPENROUTER
    capabilities = FULL_CAPABILITIES

    async def fetch(
        self, provider: Provider, client: httpx.AsyncClient
    ) -> list[CatalogModel]:
        """Read OpenRouter's catalog."""
        payload = await _get_json(client, _root(provider) + "/models", provider)
        return [_openrouter_model(entry) for entry in _entries(payload, "data")]


class OpenAICompatCatalog:
    """`GET /v1/models` — a bare list of ids, nothing more."""

    kind = OPENAI_COMPAT
    capabilities = NAME_ONLY_CAPABILITIES

    async def fetch(
        self, provider: Provider, client: httpx.AsyncClient
    ) -> list[CatalogModel]:
        """Read an OpenAI-compatible model list."""
        payload = await _get_json(client, _root(provider) + "/models", provider)
        return [
            CatalogModel(id=str(entry["id"]))
            for entry in _entries(payload, "data")
            if entry.get("id")
        ]


class OllamaCatalog:
    """`GET /api/tags` — locally pulled models, names only."""

    kind = OLLAMA
    capabilities = NAME_ONLY_CAPABILITIES

    async def fetch(
        self, provider: Provider, client: httpx.AsyncClient
    ) -> list[CatalogModel]:
        """Read the local Ollama model list."""
        base = _root(provider).removesuffix("/v1")
        payload = await _get_json(client, f"{base}/api/tags", provider)
        return [
            CatalogModel(id=str(entry["name"]))
            for entry in _entries(payload, "models")
            if entry.get("name")
        ]


#: Every adapter, by the name a provider declares in `catalog:`.
ADAPTERS: dict[str, CatalogAdapter] = {
    OPENROUTER: OpenRouterCatalog(),
    OPENAI_COMPAT: OpenAICompatCatalog(),
    OLLAMA: OllamaCatalog(),
}


def adapter_for(provider: Provider) -> CatalogAdapter:
    """Return the adapter a provider's `catalog` declaration names.

    Raises:
        CatalogError: the provider names no catalog, or an unknown one.
    """
    declared = provider.catalog
    if declared is None:
        raise CatalogError(
            f"provider '{provider.name}' declares no catalog, so its models "
            "cannot be discovered — set catalog: "
            + " | ".join(sorted(ADAPTERS))
            + ", or name the models explicitly in the pool"
        )
    adapter = ADAPTERS.get(declared)
    if adapter is None:
        raise CatalogError(
            f"provider '{provider.name}' names unknown catalog '{declared}' — "
            "known catalogs: " + ", ".join(sorted(ADAPTERS))
        )
    return adapter


# -- shared HTTP plumbing --------------------------------------------------


def _root(provider: Provider) -> str:
    base = (provider.base_url or "").rstrip("/")
    if not base:
        raise CatalogError(f"provider '{provider.name}' has no base_url to read")
    return base


async def _get_json(client: httpx.AsyncClient, url: str, provider: Provider) -> Any:
    try:
        response = await client.get(url, headers=_headers(provider))
    except httpx.TransportError as error:
        raise CatalogError(
            f"cannot reach the catalog of provider '{provider.name}' at {url}: {error}"
        ) from error
    if response.status_code >= 400:
        raise CatalogError(
            f"the catalog of provider '{provider.name}' returned "
            f"{response.status_code} for {url}"
        )
    try:
        return response.json()
    except ValueError as error:
        raise CatalogError(
            f"the catalog of provider '{provider.name}' returned a non-JSON body"
        ) from error


def _headers(provider: Provider) -> dict[str, str]:
    # Imported here: the transport module owns credential resolution, and
    # importing it at module level would tie the catalog to the transport.
    from remuda.transport.openai_compat import catalog_headers  # noqa: PLC0415

    return catalog_headers(provider)


def _entries(payload: Any, key: str) -> Sequence[Mapping[str, Any]]:
    rows = payload.get(key, []) if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _openrouter_model(entry: Mapping[str, Any]) -> CatalogModel:
    pricing = _mapping(entry.get("pricing"))
    stats = _mapping(entry.get("stats"))
    return CatalogModel(
        id=str(entry.get("id", "")),
        name=_optional_str(entry.get("name")),
        context_length=_optional_int(entry.get("context_length")),
        prompt_price_usd=_optional_float(pricing.get("prompt")),
        completion_price_usd=_optional_float(pricing.get("completion")),
        throughput=_optional_float(stats.get("throughput") or entry.get("throughput")),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
