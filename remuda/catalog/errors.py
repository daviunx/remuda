"""Errors raised while discovering a provider's models."""

from remuda.catalog.models import FILTER_DESCRIPTIONS
from remuda.errors import RemudaError


class CatalogError(RemudaError):
    """A provider's catalog could not be read."""


class UnsupportedFilterError(CatalogError):
    """A `discover:` filter this catalog cannot answer — refused, not ignored.

    Silently dropping the filter would materialize a pool the operator never
    asked for: "free models over 32k" quietly becoming "every model".
    """

    def __init__(self, provider: str, catalog: str, filters: list[str]) -> None:
        self.provider = provider
        self.catalog = catalog
        self.filters: tuple[str, ...] = tuple(filters)
        super().__init__(self._describe())

    def _describe(self) -> str:
        named = ", ".join(
            f"'{name}' ({FILTER_DESCRIPTIONS.get(name, name)})" for name in self.filters
        )
        return (
            f"provider '{self.provider}' has a '{self.catalog}' catalog, which "
            f"cannot answer {named}. Drop the filter, or name the models "
            "explicitly in the pool."
        )
