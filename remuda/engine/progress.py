"""Unbuffered per-chunk progress (FR-6).

The engine emits events; the caller decides where they go. A library that
printed to stderr itself would be unusable from a host application.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    """One chunk finished — which models answered, and how it went."""

    chunk: int
    chunks: int
    field: str
    models: tuple[str, ...] = ()
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    notes: tuple[str, ...] = ()
    distribution: tuple[tuple[str, int], ...] = ()

    def summary(self) -> str:
        """One line, in the shape an operator tails."""
        via = ", ".join(self.models) if self.models else "no model"
        line = (
            f"chunk {self.chunk}/{self.chunks} · {self.field} · via {via} · "
            f"ok {self.ok} failed {self.failed} skipped {self.skipped}"
        )
        if self.distribution:
            line += " · " + " ".join(
                f"{label}={count}" for label, count in self.distribution
            )
        return line


#: Where progress events go. Called from the event loop — keep it cheap.
ProgressCallback = Callable[[ProgressEvent], None]
