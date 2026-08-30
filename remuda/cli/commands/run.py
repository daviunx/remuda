"""`remuda run` — a job directory, an inline bulk run, or a one-shot request.

Three shapes, one engine (FR-4, FR-5, FR-6, FR-11, FR-13):

    remuda run jobs/rally-severity            a job directory on disk
    remuda run "…{{ title }}…" --input x.csv  inline bulk over a file
    remuda run "Classify: …" --pool cheap     one ad-hoc request
"""

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer

from remuda.api import OneShotFailedError, one_shot, run_job_dir
from remuda.catalog.errors import CatalogError
from remuda.cli.commands.inline_run import run_inline
from remuda.cli.console import (
    EXIT_ERROR,
    data,
    diagnostics,
    print_defects,
    print_error,
    print_header,
    print_info,
)
from remuda.cli.options import resolve_registry
from remuda.cli.progress import print_progress
from remuda.cli.reporting import print_report
from remuda.engine.plan import EngineError
from remuda.inline import InlineSpecError
from remuda.ledger.resume import ResumeRefusedError
from remuda.registry.errors import RegistryError, RegistryValidationError
from remuda.registry.registry import Registry
from remuda.rows import RowSourceError
from remuda.spec.errors import SpecValidationError
from remuda.transport.errors import TransportError

#: Every failure a run can end on that is the operator's to fix.
RUN_FAILURES = (
    CatalogError,
    EngineError,
    InlineSpecError,
    RegistryError,
    RowSourceError,
    TransportError,
)


def run(
    target: Annotated[
        str,
        typer.Argument(
            help=(
                "A job directory, or the prompt itself for an inline bulk "
                "run ([cyan]--input[/cyan]) or a one-shot request."
            )
        ),
    ],
    *,
    input_file: Annotated[
        Path | None,
        typer.Option(
            "--input",
            "-i",
            help="Input file for an inline bulk run (CSV, JSONL or JSON).",
        ),
    ] = None,
    field: Annotated[
        str,
        typer.Option("--field", help="Name of the column an inline run derives."),
    ] = "answer",
    vocabulary: Annotated[
        str | None,
        typer.Option(
            "--vocab",
            "-v",
            help="Comma-separated closed vocabulary the answer must come from.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", min=1, help="Derive only the first N rows."),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", "-O", help="Repeatable. Derive only these fields."),
    ] = None,
    pool: Annotated[
        str | None,
        typer.Option("--pool", "-p", help="Pool to answer from."),
    ] = None,
    fill_missing: Annotated[
        str | None,
        typer.Option(
            "--fill-missing",
            help="Only derive rows whose named column is empty.",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Where an inline run writes its result (default: stdout).",
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
    Run a job, an inline bulk request, or a single question.

    A target that is an existing directory is run as a job. Anything else IS
    the prompt: with [cyan]--input[/cyan] it becomes a bulk run over that
    file, without it a single request whose answer is all that reaches
    standard output.

    [bold cyan]Examples:[/bold cyan]

        # A job directory, resuming its previous run
        remuda run jobs/rally-severity

        # Inline bulk: CSV in, same CSV out with one new column
        remuda run "Severity of {{ title }}?" -i posts.csv --field severity \\
            --vocab high,low -p cheap -o enriched.csv

        # One question, answer only, composes in a pipeline
        remuda run "Name the capital of Spain" -p cheap | tr a-z A-Z
    """
    registry = _registry(config_dir, bootstrap=not no_bootstrap)
    labels = _vocabulary(vocabulary)
    job_dir = Path(target)

    if job_dir.is_dir():
        _run_job_directory(
            job_dir,
            registry=registry,
            limit=limit,
            only=only,
            pool=pool,
            fill_missing=fill_missing,
            fresh=fresh,
            runs_dir=runs_dir,
        )
        return

    if input_file is not None:
        try:
            run_inline(
                prompt=target,
                input_file=input_file,
                registry=registry,
                field=field,
                vocabulary=labels,
                pool=pool,
                limit=limit,
                fill_missing=fill_missing,
                output=output,
                fresh=fresh,
                runs_dir=runs_dir,
            )
        except RUN_FAILURES as error:
            print_error(str(error))
            raise typer.Exit(code=EXIT_ERROR) from error
        return

    _run_one_shot(target, registry=registry, pool=pool, vocabulary=labels)


def _run_one_shot(
    prompt: str,
    registry: Registry,
    pool: str | None,
    vocabulary: tuple[str, ...] | None,
) -> None:
    """FR-11: the answer, and nothing else, on standard output."""
    if not pool:
        print_error(
            "a one-shot request needs a pool to answer from",
            suggestion="Add --pool <name>, or `remuda pools show` to see them.",
        )
        raise typer.Exit(code=EXIT_ERROR)

    piped = None if sys.stdin.isatty() else sys.stdin.read()
    try:
        answer = asyncio.run(
            one_shot(
                prompt,
                registry,
                pool=pool,
                vocabulary=vocabulary,
                piped=piped,
            )
        )
    except OneShotFailedError as error:
        print_error(f"no valid answer: {error}")
        raise typer.Exit(code=EXIT_ERROR) from error
    except RUN_FAILURES as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error

    data.print(answer)


def _run_job_directory(
    job_dir: Path,
    *,
    registry: Registry,
    limit: int | None,
    only: list[str] | None,
    pool: str | None,
    fill_missing: str | None,
    fresh: bool,
    runs_dir: Path | None,
) -> None:
    print_header(f"Running {job_dir}")
    try:
        outcome = asyncio.run(
            run_job_dir(
                job_dir,
                registry=registry,
                runs_root=runs_dir,
                fresh=fresh,
                limit=limit,
                pool=pool,
                only=only,
                fill_missing=fill_missing,
                progress=print_progress,
            )
        )
    except SpecValidationError as error:
        print_defects("Job is not runnable", error.source, error.defects)
        raise typer.Exit(code=EXIT_ERROR) from error
    except ResumeRefusedError as error:
        print_error(str(error), suggestion="Re-run with --fresh to start a new run.")
        raise typer.Exit(code=EXIT_ERROR) from error
    except RUN_FAILURES as error:
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


def _registry(config_dir: list[Path] | None, bootstrap: bool) -> Registry:
    try:
        return resolve_registry(config_dir, bootstrap=bootstrap)
    except RegistryValidationError as error:
        print_defects("Registry is not usable", error.source, error.defects)
        raise typer.Exit(code=EXIT_ERROR) from error


def _vocabulary(declared: str | None) -> tuple[str, ...] | None:
    if declared is None:
        return None
    labels = tuple(label.strip() for label in declared.split(",") if label.strip())
    if not labels:
        raise typer.BadParameter("--vocab was given no labels")
    return labels
