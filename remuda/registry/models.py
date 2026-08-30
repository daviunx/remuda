"""Providers, models and pools — named once, referenced everywhere (FR-3).

Credentials never live here: a provider names the ENVIRONMENT VARIABLE its
key is read from (`key_env`), so a registry file is safe to commit and safe
to print.
"""

from typing import Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProviderKind = Literal["openai_compat", "opencode"]
PoolStrategy = Literal["scatter", "waterfall"]
DiscoverSort = Literal["throughput", "latency", "context", "price"]

#: Header names that would carry a credential inline instead of via key_env.
_CREDENTIAL_HEADERS: Final[frozenset[str]] = frozenset(
    {"authorization", "x-api-key", "api-key", "proxy-authorization"}
)


class Provider(BaseModel):
    """An endpoint remuda can talk to, and how it is authenticated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: ProviderKind = "openai_compat"
    base_url: str | None = None
    key_env: str | None = Field(
        default=None,
        description="Name of the environment variable holding the API key.",
    )
    headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=120.0, gt=0)

    @model_validator(mode="after")
    def _endpoint_and_credentials_are_declared_properly(self) -> Self:
        if self.kind == "openai_compat" and not self.base_url:
            raise ValueError(
                f"provider '{self.name}': kind 'openai_compat' needs a base_url"
            )
        if self.kind == "opencode" and self.base_url:
            raise ValueError(
                f"provider '{self.name}': kind 'opencode' runs a local command "
                "and takes no base_url"
            )
        inline = sorted(
            name for name in self.headers if name.lower() in _CREDENTIAL_HEADERS
        )
        if inline:
            raise ValueError(
                f"provider '{self.name}': header(s) {', '.join(inline)} would "
                "carry a credential inline — name the environment variable in "
                "key_env instead"
            )
        return self


class ModelConfig(BaseModel):
    """One named model: which provider serves it, and its per-run ceilings."""

    # protected_namespaces: 'model_id' is the provider's own vocabulary, not a
    # Pydantic-reserved name; without this the field raises a namespace warning.
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    name: str
    provider: str
    model_id: str = Field(description="Identifier the provider knows it by.")
    concurrency: int = Field(default=4, ge=1)
    rpm: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    context_length: int | None = Field(default=None, ge=1)
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)


class DiscoverQuery(BaseModel):
    """A query against a provider's live catalog, resolved when a run starts.

    Phase 1 stores and validates the query's shape; resolving it against a
    live catalog is the `catalog` module's job.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    free: bool | None = None
    min_context: int | None = Field(default=None, ge=1)
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    sort: DiscoverSort | None = None
    take: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _query_selects_something(self) -> Self:
        if not any(
            (
                self.free is not None,
                self.min_context is not None,
                self.include,
                self.exclude,
                self.sort is not None,
                self.take is not None,
            )
        ):
            raise ValueError(
                f"discover query against provider '{self.provider}' declares no "
                "filter — it would materialize the provider's entire catalog"
            )
        return self


class PoolEntry(BaseModel):
    """One member of a pool: a named model, or a catalog query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str | None = None
    discover: DiscoverQuery | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Self:
        if (self.model is None) == (self.discover is None):
            raise ValueError(
                "a pool entry declares either 'model' or 'discover', never "
                "both and never neither"
            )
        return self


class Pool(BaseModel):
    """An ordered set of models with a rotation strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    entries: tuple[PoolEntry, ...]
    strategy: PoolStrategy = "scatter"
    mopup: str | None = Field(
        default=None,
        description="Model that answers keys the pool exhausted.",
    )

    @model_validator(mode="after")
    def _pool_has_members(self) -> Self:
        if not self.entries:
            raise ValueError(f"pool '{self.name}': declares no models")
        return self

    @property
    def model_names(self) -> tuple[str, ...]:
        """Statically named models, in order (discover entries excluded)."""
        return tuple(entry.model for entry in self.entries if entry.model is not None)

    @property
    def discover_queries(self) -> tuple[DiscoverQuery, ...]:
        """Catalog queries to resolve when a run starts."""
        return tuple(
            entry.discover for entry in self.entries if entry.discover is not None
        )
