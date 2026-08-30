"""Inline bulk: a full run declared entirely on the command line (FR-13).

CSV in, the same CSV out with one new column, and no files written by the
operator. Internally it is an ordinary job — same engine, ladder, ledger,
resume and non-zero-on-failure contract.
"""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import typer

from remuda.api import run
from remuda.cli.console import (
    EXIT_ERROR,
    data,
    diagnostics,
    print_error,
    print_header,
    print_info,
)
from remuda.cli.progress import print_progress
from remuda.cli.reporting import print_report
from remuda.inline import default_key, input_columns, synthesize_job
from remuda.ledger.models import LedgerEntry, Report
from remuda.ledger.render import RenderRequest, render
from remuda.ledger.resume import (
    OpenedRun,
    ResumeRefusedError,
    build_lock,
    open_run,
)
from remuda.ledger.store import RUNS_DIRNAME
from remuda.registry.registry import Registry
from remuda.rows import Row, read_rows
from remuda.spec.models import Job
from remuda.version import __version__

#: Where an inline run records itself when the operator names no directory.
DEFAULT_RUNS_DIRNAME = RUNS_DIRNAME


def run_inline(
    *,
    prompt: str,
    input_file: Path,
    registry: Registry,
    field: str = "answer",
    vocabulary: Sequence[str] | None = None,
    pool: str | None = None,
    limit: int | None = None,
    fill_missing: str | None = None,
    output: Path | None = None,
    fresh: bool = False,
    runs_dir: Path | None = None,
) -> None:
    """Run an inline bulk job and render the enriched input.

    Raises:
        typer.Exit: the run failed, or could not be planned.
    """
    if not pool:
        print_error(
            "an inline run needs a pool to answer from",
            suggestion="Add --pool <name>, or `remuda pools show` to see them.",
        )
        raise typer.Exit(code=EXIT_ERROR)

    print_header(f"Running an inline job over {input_file}")
    columns = input_columns(input_file)
    job = synthesize_job(
        prompt,
        field=field,
        pool=pool,
        columns=columns,
        key=default_key(columns),
        vocabulary=vocabulary,
        input_path=str(input_file),
    )
    report, entries, rows = _execute(
        job=job,
        input_file=input_file,
        registry=registry,
        limit=limit,
        fill_missing=fill_missing,
        fresh=fresh,
        runs_dir=runs_dir,
    )

    rendered = render(
        RenderRequest(
            job=job,
            entries=entries,
            format="csv",
            enrich=True,
            rows=rows,
        )
    )
    _emit(rendered, output)
    print_report(report, None, console=diagnostics)
    if not report.is_successful:
        raise typer.Exit(code=EXIT_ERROR)


def _execute(
    *,
    job: Job,
    input_file: Path,
    registry: Registry,
    limit: int | None,
    fill_missing: str | None,
    fresh: bool,
    runs_dir: Path | None,
) -> tuple[Report, list[LedgerEntry], list[Row]]:
    """Run the synthesized job through the ordinary ledger-backed path."""
    every_row = read_rows(input_file, limit=limit)
    selected = (
        [row for row in every_row if not str(row.get(fill_missing, "")).strip()]
        if fill_missing
        else every_row
    )

    opened = _open(job, input_file, runs_dir, fresh)
    opened.store.write_job(job)
    try:
        report = asyncio.run(
            run(
                job,
                selected,
                registry,
                run_id=opened.store.run_id,
                write=opened.store.append,
                done=opened.done,
                progress=print_progress,
            )
        )
    finally:
        opened.store.close()
    opened.store.write_report(report)
    if opened.is_resumed:
        print_info(f"resumed run {opened.store.run_id}")
    return report, list(opened.store.entries()), every_row


def _open(job: Job, input_file: Path, runs_dir: Path | None, fresh: bool) -> OpenedRun:
    root = runs_dir or Path(input_file).resolve().parent / DEFAULT_RUNS_DIRNAME
    try:
        return open_run(
            runs_root=root,
            lock=build_lock(job, Path(input_file), __version__),
            fresh=fresh,
        )
    except ResumeRefusedError as error:
        print_error(str(error), suggestion="Re-run with --fresh to start a new run.")
        raise typer.Exit(code=EXIT_ERROR) from error


def _emit(rendered: str, output: Path | None) -> None:
    if output is None:
        data.print(rendered.rstrip("\n"))
        return
    output.write_text(rendered, encoding="utf-8")
    print_info(f"wrote {output}")
