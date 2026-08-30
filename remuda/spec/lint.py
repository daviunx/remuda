"""Lints that need something beyond the specification itself (FR-1, FR-8).

`Job.from_dir()` decides everything the declaration alone can answer. These
functions answer the rest — the input file's real columns, and the
placeholders a scaffolded job still carries — and return defect lines rather
than raising, so `check` can report every problem in one pass.
"""

from collections.abc import Sequence

from remuda.spec.models import Job

#: Marker written by `remuda init` wherever the operator must decide.
PLACEHOLDER = "REPLACE_ME"


def lint_against_columns(job: Job, columns: Sequence[str]) -> list[str]:
    """Check every declared reference against the input file's real columns."""
    available = set(columns)
    rendered = ", ".join(columns) or "none"
    defects: list[str] = []

    if job.input.key not in available:
        defects.append(
            f"input key column '{job.input.key}' is not in the input file "
            f"(columns: {rendered})"
        )

    for field in job.fields:
        defects.extend(_lint_field_columns(job, field.name, available, rendered))
    return defects


def lint_placeholders(job: Job) -> list[str]:
    """Report every scaffold placeholder the operator has not filled in yet."""
    defects: list[str] = []
    for field in job.fields:
        for label, value in _placeholder_candidates(job, field.name):
            if PLACEHOLDER in value:
                defects.append(
                    f"field '{field.name}': {label} still holds a scaffold "
                    f"placeholder ({_excerpt(value)}) — fill it in"
                )
    if PLACEHOLDER in job.name:
        defects.append(f"job name still holds the scaffold placeholder {PLACEHOLDER}")
    return defects


def _excerpt(value: str) -> str:
    """Quote the placeholder-bearing text so two defects never read alike."""
    for token in value.split():
        if PLACEHOLDER in token:
            return token.strip("'\",.:;")
    return PLACEHOLDER


def _lint_field_columns(
    job: Job, field_name: str, available: set[str], rendered: str
) -> list[str]:
    field = job.field(field_name)
    defects: list[str] = []
    declared_elsewhere = set(job.field_names)

    if field.prompt is not None:
        missing = [name for name in field.prompt.inputs if name not in available]
        if missing:
            defects.append(
                f"field '{field_name}': prompt input(s) "
                + ", ".join(f"'{name}'" for name in missing)
                + f" are not columns of the input file (columns: {rendered})"
            )
    if field.map_table is not None and field.map_table.lookup not in available:
        defects.append(
            f"field '{field_name}': map lookup column "
            f"'{field.map_table.lookup}' is not in the input file "
            f"(columns: {rendered})"
        )
    if (
        field.when is not None
        and field.when.source not in available
        and field.when.source not in declared_elsewhere
    ):
        defects.append(
            f"field '{field_name}': precondition source '{field.when.source}' "
            f"is neither an input column nor a declared field "
            f"(columns: {rendered})"
        )
    return defects


def _placeholder_candidates(job: Job, field_name: str) -> list[tuple[str, str]]:
    field = job.field(field_name)
    candidates: list[tuple[str, str]] = []
    if field.pool:
        candidates.append(("pool", field.pool))
    if field.prompt is not None:
        candidates.append(("prompt template", field.prompt.template))
    candidates.extend(("vocabulary", label) for label in field.vocabulary)
    return candidates
