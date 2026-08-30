"""`remuda check` — lint a job definition without calling a model (FR-1, FR-8)."""

from pathlib import Path
from typing import Annotated

import typer

from remuda.cli.console import (
    EXIT_ERROR,
    print_defects,
    print_header,
    print_success,
)
from remuda.cli.options import ConfigDirs, NoBootstrap, resolve_registry
from remuda.inspection import check_job_dir
from remuda.registry.errors import RegistryValidationError

JobDirArgument = Annotated[
    Path,
    typer.Argument(
        help="Job directory containing [cyan]job.yaml[/cyan]",
        exists=False,
    ),
]


def check(
    job_dir: JobDirArgument,
    *,
    config_dir: ConfigDirs = None,
    no_bootstrap: NoBootstrap = False,
) -> None:
    """
    Lint a job definition and its registry references.

    Checks the declaration, the input file's real columns, and every pool the
    job names — refusing with the specific defect before any model is called.
    Makes no model calls.

    [bold cyan]Examples:[/bold cyan]

        # Lint a job against the default registry layers
        remuda check jobs/rally-severity

        # Lint against a specific registry directory only
        remuda check jobs/rally-severity --config-dir ./.remuda --no-bootstrap
    """
    print_header(f"Checking {job_dir}")
    try:
        registry = resolve_registry(config_dir, should_bootstrap=not no_bootstrap)
    except RegistryValidationError as error:
        print_defects("Registry is not usable", error.source, error.defects)
        raise typer.Exit(code=EXIT_ERROR) from error

    report = check_job_dir(job_dir, registry)
    if not report.is_clean:
        print_defects("Job is not runnable", str(report.source), report.defects)
        raise typer.Exit(code=EXIT_ERROR)

    job = report.job
    if job is None:  # unreachable: a clean report always carries a job
        raise typer.Exit(code=EXIT_ERROR)
    print_success(
        f"Job '{job.name}' is runnable",
        details=[
            ("Fields", ", ".join(job.field_names)),
            ("Input", f"{job.input.path} (key: {job.input.key})"),
        ],
    )
