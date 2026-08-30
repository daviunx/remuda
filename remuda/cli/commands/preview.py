"""`remuda preview` — render prompts for the first rows, zero model calls (FR-8)."""

from pathlib import Path
from typing import Annotated

import typer

from remuda.cli.console import (
    EXIT_ERROR,
    data,
    print_defects,
    print_error,
    print_header,
    print_info,
    print_note,
)
from remuda.inspection import DEFAULT_PREVIEW_ROWS, preview_job
from remuda.rows import RowSourceError
from remuda.spec.errors import SpecValidationError


def preview(
    job_dir: Annotated[
        Path,
        typer.Argument(help="Job directory containing [cyan]job.yaml[/cyan]"),
    ],
    rows: Annotated[
        int,
        typer.Option(
            "--rows",
            "-n",
            min=1,
            help="How many input rows to render prompts for.",
        ),
    ] = DEFAULT_PREVIEW_ROWS,
    only: Annotated[
        list[str] | None,
        typer.Option(
            "--field",
            "-F",
            help="Repeatable. Preview only these fields (default: all).",
        ),
    ] = None,
) -> None:
    """
    Show the prompts a run would send, for the first rows of the input.

    Renders each field's template against real input rows using only the
    columns declared in [cyan]prompt.inputs[/cyan]. No model is called and no
    network connection is opened. The prompts go to standard output; every
    heading and note goes to standard error, so the output pipes cleanly.

    [bold cyan]Examples:[/bold cyan]

        # First three rows, every field
        remuda preview jobs/rally-severity

        # First row, one field, piped into a pager
        remuda preview jobs/rally-severity -n 1 -F severity | less
    """
    print_header(f"Previewing {job_dir}")
    try:
        report = preview_job(job_dir, rows=rows, only=only)
    except SpecValidationError as error:
        print_defects("Job is not runnable", error.source, error.defects)
        raise typer.Exit(code=EXIT_ERROR) from error
    except (RowSourceError, KeyError) as error:
        print_error(str(error).strip("'"), suggestion="Run `remuda check` first.")
        raise typer.Exit(code=EXIT_ERROR) from error

    for note in report.notes:
        print_note(note)

    for rendered in report.prompts:
        data.print(f"--- {rendered.key} :: {rendered.field} ---")
        data.print(rendered.prompt)

    print_info(
        f"rendered {len(report.prompts)} prompt(s) from up to {rows} row(s) — "
        "zero model calls"
    )
