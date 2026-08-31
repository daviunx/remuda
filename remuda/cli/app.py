"""The `remuda` command-line application (FR-8)."""

import typer
from rich.panel import Panel

from remuda import __version__
from remuda.cli.commands import (
    check,
    init,
    preview,
    render_run,
    report,
    run,
    runs_app,
    stats,
)
from remuda.cli.commands.models import models_app
from remuda.cli.commands.pools import pools_app
from remuda.cli.console import EXIT_INTERRUPTED, diagnostics

app = typer.Typer(
    name="remuda",
    help=(
        "[bold cyan]remuda[/bold cyan] — bulk LLM jobs over pools of cheap "
        "models.\n\n"
        "Ride one model until it tires, swap to the next. Run "
        "[cyan]remuda COMMAND --help[/cyan] for details.\n\n"
        "Diagnostics always go to standard error; only the answer goes to "
        "standard output, so every command composes in a pipeline."
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=True,
)

app.command("run")(run)
app.command("check")(check)
app.command("preview")(preview)
app.command("render")(render_run)
app.command("report")(report)
app.command("stats")(stats)
app.command("init")(init)
app.add_typer(runs_app, name="runs")
app.add_typer(pools_app, name="pools")
app.add_typer(models_app, name="models")


@app.command("version")
def version() -> None:
    """
    Show the installed remuda version.

    [bold cyan]Example:[/bold cyan]

        remuda version
    """
    diagnostics.print(
        Panel.fit(
            f"[bold cyan]remuda[/bold cyan]\n\n[bold]Version:[/bold] {__version__}",
            title="Version",
            border_style="cyan",
        )
    )


def main() -> None:
    """Entry point for the `remuda` console script."""
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - operator action
        diagnostics.print("\n[yellow]⚠️  Interrupted[/yellow]")
        raise SystemExit(EXIT_INTERRUPTED) from None


if __name__ == "__main__":  # pragma: no cover - module execution
    main()
