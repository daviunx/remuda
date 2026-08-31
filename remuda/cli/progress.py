"""Where the engine's progress events go on the command line (FR-6)."""

from remuda.cli.console import diagnostics
from remuda.engine.progress import ProgressEvent


def print_progress(event: ProgressEvent) -> None:
    """One unbuffered line per chunk, on stderr — never on the answer stream."""
    diagnostics.print(f"[dim]{event.summary()}[/dim]")
    for note in event.notes:
        diagnostics.print(f"[yellow]  ⚠️  {note}[/yellow]")
