"""`remuda report` — re-print a finished run's report (FR-6)."""

from pathlib import Path
from typing import Annotated

import typer

from remuda.cli.console import EXIT_ERROR, data, print_error, print_info
from remuda.cli.reporting import print_report
from remuda.ledger.store import REPORT_FILENAME, LedgerError, RunStore


def report(
    run_dir: Annotated[
        Path, typer.Argument(help="Run directory under [cyan].runs/[/cyan]")
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", "-j", help="Emit the raw report.json instead."),
    ] = False,
) -> None:
    """
    Re-print the report of a finished run, without re-running anything.

    [bold cyan]Examples:[/bold cyan]

        # Human-readable summary
        remuda report .runs/rally-severity/20260830T120000Z

        # The raw record, for a script
        remuda report .runs/rally-severity/latest --json | jq .models
    """
    try:
        store = RunStore.open(run_dir)
        persisted = store.read_report()
    except LedgerError as error:
        print_error(
            str(error),
            suggestion=f"Point at a run directory containing {REPORT_FILENAME}.",
        )
        raise typer.Exit(code=EXIT_ERROR) from error

    print_info(f"run {store.run_id}")
    if as_json:
        data.print(persisted.model_dump_json(indent=2))
        return
    print_report(persisted, store.run_dir, console=data)
