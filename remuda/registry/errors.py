"""Errors raised while building or consulting the registry."""

from collections.abc import Sequence

from remuda.errors import RemudaError


class RegistryError(RemudaError):
    """A registry lookup failed."""


class RegistryValidationError(RegistryError):
    """The registry is internally inconsistent, with every defect named."""

    def __init__(self, source: str, defects: Sequence[str]) -> None:
        self.source = source
        self.defects: tuple[str, ...] = tuple(defects)
        super().__init__(self._describe())

    def _describe(self) -> str:
        count = len(self.defects)
        noun = "defect" if count == 1 else "defects"
        lines = [f"registry ({self.source}) is invalid ({count} {noun}):"]
        lines.extend(f"  - {defect}" for defect in self.defects)
        return "\n".join(lines)
