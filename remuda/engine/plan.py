"""Deciding what work a run consists of, before any model is called.

Field ordering, precondition skips and dependency propagation are pure
decisions over the spec and the rows — kept out of the runner so they can be
tested without an event loop.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from remuda.errors import RemudaError
from remuda.spec.models import FieldSpec, Job
from remuda.validate.preconditions import precondition_met


class EngineError(RemudaError):
    """A run could not be planned or executed."""


@dataclass(frozen=True)
class Skip:
    """A (row, field) the run decided not to derive — counted, not failed."""

    key: str
    field: str
    reason: str


def select_fields(job: Job, only: Sequence[str] | None = None) -> tuple[FieldSpec, ...]:
    """Return the fields to derive, dependencies before dependents.

    Raises:
        EngineError: a selected field's dependency is not itself selected, or
            a named field is not declared.
    """
    if only:
        unknown = [name for name in only if name not in job.field_names]
        if unknown:
            raise EngineError(
                "unknown field(s) "
                + ", ".join(f"'{name}'" for name in unknown)
                + f" — this job declares: {', '.join(job.field_names)}"
            )
        selected = set(only)
        for name in only:
            dependency = job.field(name).depends_on
            if dependency is not None and dependency not in selected:
                raise EngineError(
                    f"field '{name}' depends on '{dependency}' — add "
                    f"'{dependency}' to the selection, or select neither"
                )
    else:
        selected = set(job.field_names)

    independent = [f for f in job.fields if f.name in selected and not f.depends_on]
    dependent = [f for f in job.fields if f.name in selected and f.depends_on]
    return (*independent, *dependent)


def eligibility(
    field: FieldSpec,
    key: str,
    row: Mapping[str, Any],
    derived: Mapping[str, Any],
) -> Skip | None:
    """Return why this (row, field) is skipped, or None when it is eligible."""
    dependency = field.depends_on
    if dependency is not None and dependency not in derived:
        return Skip(
            key=key,
            field=field.name,
            reason=f"'{dependency}' was not derived for this row",
        )
    if field.when is None:
        return None
    source = field.when.source
    value = derived[source] if source in derived else row.get(source)
    is_met, reason = precondition_met(field.when, value)
    if is_met:
        return None
    return Skip(key=key, field=field.name, reason=reason)
