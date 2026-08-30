"""Per-provider catalog discovery behind `discover:` (FR-3)."""

from remuda.catalog.adapters import (
    ADAPTERS,
    OLLAMA,
    OPENAI_COMPAT,
    OPENROUTER,
    CatalogAdapter,
    OllamaCatalog,
    OpenAICompatCatalog,
    OpenRouterCatalog,
    adapter_for,
)
from remuda.catalog.errors import CatalogError, UnsupportedFilterError
from remuda.catalog.models import (
    FULL_CAPABILITIES,
    NAME_ONLY_CAPABILITIES,
    CatalogModel,
)
from remuda.catalog.resolve import (
    ResolvedMember,
    ResolvedPool,
    apply_query,
    member_to_model,
    requested_filters,
    resolve_pool,
    to_model_configs,
)

__all__ = [
    "ADAPTERS",
    "FULL_CAPABILITIES",
    "NAME_ONLY_CAPABILITIES",
    "OLLAMA",
    "OPENAI_COMPAT",
    "OPENROUTER",
    "CatalogAdapter",
    "CatalogError",
    "CatalogModel",
    "OllamaCatalog",
    "OpenAICompatCatalog",
    "OpenRouterCatalog",
    "ResolvedMember",
    "ResolvedPool",
    "UnsupportedFilterError",
    "adapter_for",
    "apply_query",
    "member_to_model",
    "requested_filters",
    "resolve_pool",
    "to_model_configs",
]
