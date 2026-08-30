"""Building a job from command-line parts (FR-11, FR-13).

One-shot and inline bulk are not a second execution path: both synthesize an
ordinary one-field job and hand it to the same runner, ladder, validators and
ledger the job-directory path uses.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from remuda.errors import RemudaError
from remuda.rows import read_columns
from remuda.spec.models import ExecutionSpec, FieldSpec, InputSpec, Job
from remuda.spec.prompt import template_variables

#: Column a one-shot exposes its piped stdin under.
ONE_SHOT_INPUT = "input"

#: Key column of the single synthetic row a one-shot derives.
ONE_SHOT_KEY = "id"

INLINE_JOB_NAME = "inline"
ONE_SHOT_JOB_NAME = "one-shot"


class InlineSpecError(RemudaError):
    """A job could not be built from the given command-line parts."""


def synthesize_job(
    prompt: str,
    *,
    field: str = "answer",
    pool: str,
    columns: Sequence[str],
    key: str,
    vocabulary: Sequence[str] | None = None,
    name: str = INLINE_JOB_NAME,
    input_path: str = "-",
    records_per_call: int = 1,
    workers: int = 4,
    attempts_per_model: int = 2,
) -> Job:
    """Build a one-field job around `prompt`.

    The prompt's inputs are DERIVED from the template and checked against the
    input's real columns: the allowlist firewall is preserved, not waived,
    because the operator has nowhere else to declare it.

    Raises:
        InlineSpecError: the template reads something the input lacks, or the
            resulting job is not valid.
    """
    if not prompt.strip():
        raise InlineSpecError("the prompt is empty — there is nothing to ask")

    available = tuple(columns)
    if key not in available:
        raise InlineSpecError(
            f"key column '{key}' is not in the input "
            f"(columns: {', '.join(available) or 'none'})"
        )

    inputs = _declared_inputs(prompt, available)
    declaration: dict[str, Any] = {
        "name": field,
        "kind": "classify" if vocabulary else "generate",
        "pool": pool,
        "prompt": {"inputs": inputs, "template": prompt},
    }
    if vocabulary:
        declaration["vocabulary"] = tuple(vocabulary)

    try:
        return Job(
            name=name,
            input=InputSpec(path=input_path, key=key),
            fields=(FieldSpec.model_validate(declaration),),
            execution=ExecutionSpec(
                records_per_call=records_per_call,
                workers=workers,
                attempts_per_model=attempts_per_model,
            ),
        )
    except ValueError as error:
        raise InlineSpecError(f"the inline job is not valid: {error}") from error


def one_shot_job(
    prompt: str,
    *,
    pool: str,
    vocabulary: Sequence[str] | None = None,
    attempts_per_model: int = 2,
) -> Job:
    """Build the single-row job behind a one-shot request (FR-11)."""
    return synthesize_job(
        prompt,
        pool=pool,
        columns=(ONE_SHOT_KEY, ONE_SHOT_INPUT),
        key=ONE_SHOT_KEY,
        vocabulary=vocabulary,
        name=ONE_SHOT_JOB_NAME,
        attempts_per_model=attempts_per_model,
    )


def one_shot_row(piped: str | None) -> dict[str, str]:
    """The single row a one-shot derives, carrying any piped stdin."""
    return {ONE_SHOT_KEY: "1", ONE_SHOT_INPUT: (piped or "").strip()}


def input_columns(path: Path) -> tuple[str, ...]:
    """The columns of an inline run's input file.

    Raises:
        InlineSpecError: the file has no columns to read.
    """
    columns = read_columns(Path(path))
    if not columns:
        raise InlineSpecError(f"input file '{path}' declares no columns")
    return columns


def default_key(columns: Sequence[str]) -> str:
    """The key column an inline run assumes: the first one.

    Raises:
        InlineSpecError: there are no columns to choose from.
    """
    if not columns:
        raise InlineSpecError("the input has no columns, so it has no key")
    return columns[0]


def _declared_inputs(prompt: str, columns: Sequence[str]) -> tuple[str, ...]:
    variables = template_variables(prompt)
    unknown = sorted(variables - set(columns))
    if unknown:
        raise InlineSpecError(
            "the prompt reads "
            + ", ".join(f"'{name}'" for name in unknown)
            + ", which the input does not have (columns: "
            + (", ".join(columns) or "none")
            + ")"
        )
    return tuple(name for name in columns if name in variables)
