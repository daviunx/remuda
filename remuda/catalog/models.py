"""What a provider's catalog can tell us about a model (FR-3).

Catalogs differ enormously: OpenRouter reports pricing, context and
throughput; a bare `/v1/models` reports names. Every adapter fills in what it
knows and declares what it cannot answer, so a filter is never silently
ignored.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, model_validator

#: The filters a `discover:` query can carry, by the name it uses.
FREE: Final = "free"
MIN_CONTEXT: Final = "min_context"
INCLUDE: Final = "include"
EXCLUDE: Final = "exclude"
SORT: Final = "sort"
TAKE: Final = "take"

#: Filters every catalog can answer from model ids alone.
NAME_ONLY_CAPABILITIES: Final[frozenset[str]] = frozenset({INCLUDE, EXCLUDE, TAKE})

#: Everything a rich catalog (OpenRouter) can answer.
FULL_CAPABILITIES: Final[frozenset[str]] = NAME_ONLY_CAPABILITIES | {
    FREE,
    MIN_CONTEXT,
    SORT,
}

#: Human wording for each filter, used in refusal messages.
FILTER_DESCRIPTIONS: Final[dict[str, str]] = {
    FREE: "free-tier filtering (needs pricing)",
    MIN_CONTEXT: "a minimum context length",
    INCLUDE: "name matching",
    EXCLUDE: "name exclusion",
    SORT: "sorting (needs pricing, context or throughput)",
    TAKE: "taking the first N",
}


class CatalogModel(BaseModel):
    """One model as a provider's catalog describes it."""

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    id: str
    name: str | None = None
    context_length: int | None = None
    prompt_price_usd: float | None = None
    completion_price_usd: float | None = None
    throughput: float | None = None

    @property
    def is_free(self) -> bool:
        """True only when the catalog reports both prices as zero."""
        if self.prompt_price_usd is None or self.completion_price_usd is None:
            return False
        return self.prompt_price_usd == 0 and self.completion_price_usd == 0

    @property
    def has_pricing(self) -> bool:
        """True when the catalog reported a price for this model."""
        return self.prompt_price_usd is not None

    @model_validator(mode="after")
    def _identifier_is_present(self) -> Self:
        if not self.id.strip():
            raise ValueError("catalog model has no id")
        return self
