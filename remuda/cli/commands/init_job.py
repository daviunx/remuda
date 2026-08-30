"""`remuda init` — scaffold a commented job directory (FR-8)."""

from pathlib import Path
from typing import Annotated

import typer

from remuda.cli.console import EXIT_ERROR, print_error, print_header, print_success
from remuda.cli.scaffold import ScaffoldError, scaffold_job_dir
from remuda.spec.lint import PLACEHOLDER


def init(
    directory: Annotated[
        Path,
        typer.Argument(help="Directory to create the job in."),
    ],
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            "-N",
            help="Job name (default: the directory name).",
        ),
    ] = None,
) -> None:
    """
    Scaffold a new job directory with a commented example definition.

    Writes [cyan]job.yaml[/cyan], a vocabulary file and a sample input file.
    Everything you must decide is marked [yellow]REPLACE_ME[/yellow]; running
    [cyan]remuda check[/cyan] on the fresh directory lists exactly what is
    still missing.

    [bold cyan]Examples:[/bold cyan]

        # Scaffold, then see what is left to fill in
        remuda init jobs/my-job && remuda check jobs/my-job

        # Scaffold with an explicit job name
        remuda init jobs/my-job --name severity-backfill
    """
    print_header(f"Scaffolding {directory}")
    try:
        written = scaffold_job_dir(directory, name)
    except (ScaffoldError, OSError) as error:
        print_error(str(error), suggestion="Choose a directory with no job.yaml.")
        raise typer.Exit(code=EXIT_ERROR) from error

    print_success(
        "Job directory scaffolded",
        details=[
            ("Files", ", ".join(str(path) for path in written)),
            (
                "Next",
                f"replace every {PLACEHOLDER}, then run: remuda check {directory}",
            ),
        ],
    )
