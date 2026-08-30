"""`when:` precondition evaluation — a skip is counted, never a failure (FR-2)."""

from typing import Any

from remuda.spec.shapes import Precondition


def precondition_met(precondition: Precondition, value: Any) -> tuple[bool, str]:
    """Return whether the guard passes, and the reason when it does not."""
    text = "" if value is None else str(value)
    stripped = text.strip()

    if precondition.not_empty and not stripped:
        return False, f"'{precondition.source}' is empty"
    if precondition.min_length is not None and len(stripped) < precondition.min_length:
        return (
            False,
            f"'{precondition.source}' is {len(stripped)} characters, "
            f"below the required {precondition.min_length}",
        )
    if precondition.equals is not None and stripped != precondition.equals:
        return (
            False,
            f"'{precondition.source}' is not '{precondition.equals}'",
        )
    if precondition.one_of is not None and stripped not in precondition.one_of:
        return (
            False,
            f"'{precondition.source}' is not one of {', '.join(precondition.one_of)}",
        )
    return True, ""
