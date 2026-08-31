"""`remuda render` — turn a finished run into csv, jsonl or json (FR-10)."""

from pathlib import Path
from typing import Annotated

import typer

from remuda.cli.console import EXIT_ERROR, data, print_error, print_info
from remuda.ledger.render import RenderError, RenderFormat, RenderRequest, render
from remuda.ledger.store import LedgerError, RunStore
from remuda.rows import RowSourceError, read_rows

STDOUT = Path("-")

RunDirArgument = Annotated[
    Path, typer.Argument(help="Run directory under [cyan].runs/[/cyan]")
]
FormatOption = Annotated[
    RenderFormat,
    typer.Option("--format", "-f", help="csv (default), jsonl or json."),
]
OutputOption = Annotated[
    Path,
    typer.Option("--output", "-o", help="File to write, or - for stdout."),
]
EnrichOption = Annotated[
    bool,
    typer.Option(
        "--enrich",
        help="Return the input file with the derived columns added.",
    ),
]
FillMissingOption = Annotated[
    bool,
    typer.Option("--fill-missing", help="Never overwrite a non-empty cell."),
]
PassthroughOption = Annotated[
    list[str] | None,
    typer.Option(
        "--passthrough",
        "-P",
        help="Repeatable input column to carry through; * for all.",
    ),
]
FieldsOption = Annotated[
    list[str] | None,
    typer.Option("--field", "-F", help="Repeatable. Render only these fields."),
]


def render_run(
    run_dir: RunDirArgument,
    *,
    output_format: FormatOption = "csv",
    output: OutputOption = STDOUT,
    enrich: EnrichOption = False,
    fill_missing: FillMissingOption = False,
    passthrough: PassthroughOption = None,
    only: FieldsOption = None,
) -> None:
    """
    Render a finished run's results. No model is called.

    Enrich mode returns the input file exactly as it was, with the derived
    columns appended or updated and every other cell untouched.

    [bold cyan]Examples:[/bold cyan]

        # Key column plus derived fields, to stdout
        remuda render .runs/rally-severity/20260830T120000Z

        # The input CSV with the new column added, written back to a file
        remuda render .runs/rally-severity/latest --enrich -o enriched.csv

        # Every input column, as JSON lines
        remuda render .runs/rally-severity/latest -f jsonl -P '*'
    """
    try:
        store = RunStore.open(run_dir)
        job = store.read_job()
        rows = _input_rows(store, is_needed=enrich or bool(passthrough))
        rendered = render(
            RenderRequest(
                job=job,
                entries=store.entries(),
                format=output_format,
                fields=only,
                passthrough=tuple(passthrough or ()),
                enrich=enrich,
                fill_missing=fill_missing,
                rows=rows,
            )
        )
    except (LedgerError, RenderError, RowSourceError) as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error

    if output == STDOUT:
        data.print(rendered.rstrip("\n"))
        return
    output.write_text(rendered, encoding="utf-8")
    print_info(f"wrote {output}")


def _input_rows(store: RunStore, is_needed: bool) -> list[dict[str, object]] | None:
    """Read the run's input file — required by enrich and passthrough."""
    if not is_needed:
        return None
    declared = store.read_lock().input_path
    if declared is None:
        raise RenderError(
            "this run had no input file (its rows came from a host "
            "application), so enrich and passthrough have nothing to read"
        )
    path = Path(declared)
    if not path.is_file():
        raise RenderError(
            f"the run's input file '{path}' is no longer there — enrich and "
            "passthrough need it to preserve the original columns"
        )
    return read_rows(path)
