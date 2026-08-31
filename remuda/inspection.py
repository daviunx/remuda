"""Zero-network inspection of a job directory (FR-1, FR-8).

`check_job_dir` answers "would this job run?" and `preview_job` answers "what
exactly would be sent?" — both without opening a socket. The CLI is a thin
adapter over these; a host embedding remuda calls them directly.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from remuda.registry.registry import Registry
from remuda.rows import RowSourceError, read_columns, read_rows, row_key
from remuda.spec.errors import PromptRenderError, SpecValidationError
from remuda.spec.lint import lint_against_columns, lint_placeholders
from remuda.spec.models import Job

DEFAULT_PREVIEW_ROWS = 3


@dataclass(frozen=True)
class CheckReport:
    """The outcome of linting a job directory against its input and registry."""

    source: Path
    job: Job | None
    defects: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        """True when the job is ready to run."""
        return not self.defects


@dataclass(frozen=True)
class RenderedPrompt:
    """One prompt as it would be sent, for one row and one field."""

    key: str
    field: str
    prompt: str


@dataclass(frozen=True)
class PreviewReport:
    """Rendered prompts plus anything the operator should know about them."""

    job: Job
    prompts: tuple[RenderedPrompt, ...] = ()
    notes: tuple[str, ...] = ()


def check_job_dir(job_dir: Path, registry: Registry) -> CheckReport:
    """Lint a job directory: its spec, its input file, and its pool names."""
    directory = Path(job_dir)
    try:
        job = Job.from_dir(directory)
    except SpecValidationError as error:
        return CheckReport(source=directory, job=None, defects=error.defects)

    defects: list[str] = list(lint_placeholders(job))
    defects.extend(_input_defects(directory, job))
    defects.extend(registry.check_job(job))
    return CheckReport(source=directory, job=job, defects=tuple(defects))


def preview_job(
    job_dir: Path,
    rows: int = DEFAULT_PREVIEW_ROWS,
    only: Sequence[str] | None = None,
) -> PreviewReport:
    """Render the prompts for the first `rows` input rows. No model is called.

    Raises:
        SpecValidationError: the job directory is misdeclared.
        RowSourceError: the input file cannot be read.
        KeyError: `only` names a field the job does not declare.
    """
    job = Job.from_dir(job_dir)
    selected = _select_fields(job, only)
    notes: list[str] = []
    prompts: list[RenderedPrompt] = []

    input_path = Path(job_dir) / job.input.path
    for row in read_rows(input_path, job.input.format, limit=rows):
        key = row_key(row, job.input.key)
        for name in selected:
            field = job.field(name)
            if field.prompt is None:
                continue
            try:
                prompts.append(
                    RenderedPrompt(key=key, field=name, prompt=field.render(row))
                )
            except PromptRenderError as error:
                notes.append(f"row '{key}', field '{name}': {error}")

    notes.extend(
        f"field '{name}' is a deterministic lookup (kind 'map') — no prompt to preview"
        for name in selected
        if job.field(name).prompt is None
    )
    return PreviewReport(job=job, prompts=tuple(prompts), notes=tuple(notes))


def _select_fields(job: Job, only: Sequence[str] | None) -> tuple[str, ...]:
    if not only:
        return job.field_names
    for name in only:
        job.field(name)  # raises KeyError naming the declared fields
    return tuple(only)


def _input_defects(directory: Path, job: Job) -> list[str]:
    input_path = directory / job.input.path
    try:
        columns = read_columns(input_path, job.input.format)
    except RowSourceError as error:
        return [f"input '{job.input.path}': {error}"]
    return lint_against_columns(job, columns)
