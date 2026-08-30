"""Turning a `discover:` query into an ordered, recorded model list (FR-3).

Resolution happens once, when a run starts. The result is written beside the
run so a resume reuses exactly the models the first attempt used — free-tier
membership shifts week to week, and a resumed run must not quietly change
which models answered it.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from remuda.catalog.adapters import adapter_for
from remuda.catalog.errors import CatalogError, UnsupportedFilterError
from remuda.catalog.models import (
    EXCLUDE,
    FREE,
    INCLUDE,
    MIN_CONTEXT,
    SORT,
    TAKE,
    CatalogModel,
)
from remuda.registry.models import DiscoverQuery, ModelConfig, Pool, Provider
from remuda.registry.registry import Registry

MemberSource = Literal["declared", "discovered", "mopup"]


class ResolvedMember(BaseModel):
    """One model a pool resolved to, and where it came from."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    name: str
    provider: str
    model_id: str
    source: MemberSource = "declared"
    context_length: int | None = None
    prompt_price_usd: float | None = None
    completion_price_usd: float | None = None


class ResolvedPool(BaseModel):
    """A pool's materialized membership, snapshotted with the run."""

    model_config = ConfigDict(extra="forbid")

    pool: str
    strategy: str = "scatter"
    members: list[ResolvedMember] = Field(default_factory=list)
    mopup: ResolvedMember | None = None
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def member_names(self) -> tuple[str, ...]:
        """Every member name, in ladder order."""
        return tuple(member.name for member in self.members)


def requested_filters(query: DiscoverQuery) -> list[str]:
    """Every filter this query actually carries."""
    declared: list[str] = []
    if query.free is not None:
        declared.append(FREE)
    if query.min_context is not None:
        declared.append(MIN_CONTEXT)
    if query.include:
        declared.append(INCLUDE)
    if query.exclude:
        declared.append(EXCLUDE)
    if query.sort is not None:
        declared.append(SORT)
    if query.take is not None:
        declared.append(TAKE)
    return declared


def apply_query(
    query: DiscoverQuery,
    models: Sequence[CatalogModel],
    capabilities: frozenset[str],
    provider: str,
    catalog: str,
) -> list[CatalogModel]:
    """Filter and order a catalog by the query.

    Raises:
        UnsupportedFilterError: the catalog cannot answer a declared filter.
    """
    unsupported = [
        name for name in requested_filters(query) if name not in capabilities
    ]
    if unsupported:
        raise UnsupportedFilterError(provider, catalog, unsupported)

    selected = list(models)
    if query.free:
        selected = [model for model in selected if model.is_free]
    if query.min_context is not None:
        selected = [
            model
            for model in selected
            if model.context_length is not None
            and model.context_length >= query.min_context
        ]
    if query.include:
        selected = [
            model
            for model in selected
            if any(fragment in model.id for fragment in query.include)
        ]
    if query.exclude:
        selected = [
            model
            for model in selected
            if not any(fragment in model.id for fragment in query.exclude)
        ]
    if query.sort is not None:
        selected = _sorted(selected, query.sort, provider, catalog)
    if query.take is not None:
        selected = selected[: query.take]
    return selected


async def resolve_pool(
    pool: Pool,
    registry: Registry,
    client: httpx.AsyncClient,
) -> ResolvedPool:
    """Materialize a pool: declared models first, then discovered ones.

    Raises:
        CatalogError: a catalog is unreachable, unnamed, or cannot answer a
            declared filter.
        RegistryError: the pool names a model or provider that is not
            registered.
    """
    members = [_declared(registry, name) for name in pool.model_names]
    for query in pool.discover_queries:
        members.extend(await _discover(query, registry, client))

    unique: list[ResolvedMember] = []
    seen: set[str] = set()
    for member in members:
        if member.name in seen:
            continue
        seen.add(member.name)
        unique.append(member)
    return ResolvedPool(
        pool=pool.name,
        strategy=pool.strategy,
        members=unique,
        mopup=_declared(registry, pool.mopup, "mopup") if pool.mopup else None,
    )


def to_model_configs(resolved: ResolvedPool) -> list[ModelConfig]:
    """Every resolved member as a registry model the engine can build a lane from."""
    return [
        member_to_model(member) for member in (*resolved.members, *_mopup(resolved))
    ]


def _mopup(resolved: ResolvedPool) -> tuple[ResolvedMember, ...]:
    return (resolved.mopup,) if resolved.mopup is not None else ()


def member_to_model(member: ResolvedMember) -> ModelConfig:
    """The registry model a resolved member stands for."""
    return ModelConfig(
        name=member.name,
        provider=member.provider,
        model_id=member.model_id,
        context_length=member.context_length,
        prompt_price_usd=member.prompt_price_usd,
        completion_price_usd=member.completion_price_usd,
    )


def _declared(
    registry: Registry, name: str, source: MemberSource = "declared"
) -> ResolvedMember:
    model = registry.model(name)
    return ResolvedMember(
        name=model.name,
        provider=model.provider,
        model_id=model.model_id,
        source=source,
        context_length=model.context_length,
        prompt_price_usd=model.prompt_price_usd,
        completion_price_usd=model.completion_price_usd,
    )


async def _discover(
    query: DiscoverQuery, registry: Registry, client: httpx.AsyncClient
) -> list[ResolvedMember]:
    provider = registry.provider(query.provider)
    adapter = adapter_for(provider)
    catalog = await adapter.fetch(provider, client)
    selected = apply_query(
        query, catalog, adapter.capabilities, provider.name, adapter.kind
    )
    if not selected:
        raise CatalogError(
            f"the discover query against provider '{provider.name}' matched no "
            "models — relax the filters, or name the models explicitly"
        )
    return [_discovered(model, provider) for model in selected]


def _discovered(model: CatalogModel, provider: Provider) -> ResolvedMember:
    return ResolvedMember(
        name=model.id,
        provider=provider.name,
        model_id=model.id,
        source="discovered",
        context_length=model.context_length,
        prompt_price_usd=model.prompt_price_usd,
        completion_price_usd=model.completion_price_usd,
    )


def _sorted(
    models: Sequence[CatalogModel], key: str, provider: str, catalog: str
) -> list[CatalogModel]:
    """Order by the requested key, refusing a key the catalog cannot answer."""
    orderings: dict[str, tuple[Callable[[CatalogModel], float | int | None], bool]] = {
        "throughput": (lambda model: model.throughput, True),
        "context": (lambda model: model.context_length, True),
        "price": (lambda model: model.prompt_price_usd, False),
    }
    ordering = orderings.get(key)
    if ordering is None or not any(ordering[0](model) is not None for model in models):
        raise UnsupportedFilterError(provider, catalog, [SORT])
    extract, descending = ordering
    known = [model for model in models if extract(model) is not None]
    unknown = [model for model in models if extract(model) is None]
    known.sort(key=lambda model: extract(model) or 0, reverse=descending)
    return [*known, *unknown]
