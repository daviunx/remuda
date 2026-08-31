"""Errors raised while loading, linting or rendering a job specification."""

from collections.abc import Sequence
from pathlib import Path

from remuda.errors import RemudaError


class SpecValidationError(RemudaError):
    """A job specification was refused, with every defect named.

    The message lists one line per defect so an operator can fix the whole
    spec in a single pass instead of one refusal per run (FR-1).
    """

    def __init__(self, source: Path | str, defects: Sequence[str]) -> None:
        self.source = str(source)
        self.defects: tuple[str, ...] = tuple(defects)
        super().__init__(self._describe())

    def _describe(self) -> str:
        count = len(self.defects)
        noun = "defect" if count == 1 else "defects"
        lines = [f"job spec at {self.source} is invalid ({count} {noun}):"]
        lines.extend(f"  - {defect}" for defect in self.defects)
        return "\n".join(lines)


class PromptRenderError(RemudaError):
    """A prompt template could not be rendered from the declared inputs."""
