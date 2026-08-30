"""`remuda run` — execute a job over its input file (FR-4, FR-5, FR-6)."""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from remuda.api import run_job_dir
from remuda.catalog.errors import CatalogError
from remuda.cli.console import (
    EXIT_ERROR,
    diagnostics,
    print_defects,
    print_error,
    print_header,
    print_info,
)
from remuda.cli.options import resolve_registry
from remuda.cli.reporting import print_report
from remuda.engine.plan import EngineError
from remuda.engine.progress import ProgressEvent
from remuda.ledger.resume import ResumeRefusedError
from remuda.registry.errors import RegistryValidationError
from remuda.rows import RowSourceError
from remuda.spec.errors import SpecValidationError
from remuda.transport.errors import TransportError


def run(
    job_dir: Annotated[
        Path, typer.Argument(help="Job directory containing [cyan]job.yaml[/cyan]")
    ],
    *,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", min=1, help="Derive only the first N rows."),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--field", "-F", help="Repeatable. Derive only these fields."),
    ] = None,
    pool: Annotated[
        str | None,
        typer.Option("--pool", "-p", help="Override the pool every field names."),
    ] = None,
    fill_missing: Annotated[
        str | None,
        typer.Option(
            "--fill-missing",
            help="Only derive rows whose named column is empty.",
        ),
    ] = None,
    fresh: Annotated[
        bool,
        typer.Option("--fresh", help="Start a new run instead of resuming."),
    ] = False,
    runs_dir: Annotated[
        Path | None,
        typer.Option("--runs-dir", help="Where run directories live."),
    ] = None,
    config_dir: Annotated[
        list[Path] | None,
        typer.Option("--config-dir", "-c", help="Registry layer. Repeatable."),
    ] = None,
    no_bootstrap: Annotated[
        bool,
        typer.Option(
            "--no-bootstrap",
            help="Ignore providers implied by the environment.",
        ),
    ] = False,
) -> None:
    """
    Run a job: derive every declared field for every input row.

    Resumes the job's previous run when the job definition and input are
    unchanged, recomputing only what is left. Progress goes to standard error
    as each chunk finishes. Any failed key makes the run exit non-zero.

    [bold cyan]Examples:[/bold cyan]

        # Run the whole job, resuming if a previous run is incomplete
        remuda run jobs/rally-severity

        # Rehearse on ten rows against a specific pool
        remuda run jobs/rally-severity --limit 10 --pool free-fast

        # Start over, ignoring the previous run
        remuda run jobs/rally-severity --fresh
    """
    print_header(f"Running {job_dir}")
    try:
        outcome = asyncio.run(
            run_job_dir(
                job_dir,
                registry=resolve_registry(config_dir, bootstrap=not no_bootstrap),
                runs_root=runs_dir,
                fresh=fresh,
                limit=limit,
                pool=pool,
                only=only,
                fill_missing=fill_missing,
                progress=_print_progress,
            )
        )
    except SpecValidationError as error:
        print_defects("Job is not runnable", error.source, error.defects)
        raise typer.Exit(code=EXIT_ERROR) from error
    except RegistryValidationError as error:
        print_defects("Registry is not usable", error.source, error.defects)
        raise typer.Exit(code=EXIT_ERROR) from error
    except ResumeRefusedError as error:
        print_error(str(error), suggestion="Re-run with --fresh to start a new run.")
        raise typer.Exit(code=EXIT_ERROR) from error
    except (EngineError, RowSourceError, CatalogError, TransportError) as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error
    except KeyboardInterrupt as error:  # pragma: no cover - operator action
        print_error("Interrupted — re-run the same command to resume.")
        raise typer.Exit(code=130) from error

    for announcement in outcome.announcements:
        print_info(announcement)
    if outcome.is_resumed:
        print_info(f"resumed run {outcome.store.run_id}")
    print_report(outcome.report, outcome.store.run_dir, console=diagnostics)
    if not outcome.report.is_successful:
        raise typer.Exit(code=EXIT_ERROR)


def _print_progress(event: ProgressEvent) -> None:
    """One unbuffered line per chunk, on stderr (FR-6)."""
    diagnostics.print(f"[dim]{event.summary()}[/dim]")
    for note in event.notes:
        diagnostics.print(f"[yellow]  ⚠️  {note}[/yellow]")
