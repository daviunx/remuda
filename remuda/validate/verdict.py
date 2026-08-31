"""The three outcomes every (row, field) result can take (FR-2)."""

from dataclasses import dataclass
from typing import Any, Literal

#: Every (row, field) result is exactly one of these.
Outcome = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True)
class Verdict:
    """A validator's judgement of one raw answer.

    `repair` is the feedback handed back to the model on the next attempt: it
    says what was wrong in the model's own terms, never how remuda works.
    """

    is_valid: bool
    value: Any = None
    repair: str | None = None

    @classmethod
    def valid(cls, value: Any) -> "Verdict":
        """The answer is acceptable; `value` is its normalized form."""
        return cls(is_valid=True, value=value)

    @classmethod
    def invalid(cls, repair: str) -> "Verdict":
        """The answer is unusable; `repair` tells the model what to fix."""
        return cls(is_valid=False, repair=repair)
