"""Two output streams, kept strictly apart (FR-11).

Stdout carries the ANSWER — rendered prompts, results, anything a pipeline
consumes. Everything else — progress, refusals, summaries — goes to stderr,
so `remuda ... | other-command` never receives a diagnostic.
"""

from collections.abc import Sequence
from typing import Final

from rich.console import Console
from rich.panel import Panel

EXIT_SUCCESS: Final = 0
EXIT_ERROR: Final = 1
EXIT_INTERRUPTED: Final = 130

#: Diagnostics, progress and refusals. NEVER the answer.
diagnostics: Final = Console(stderr=True)

#: The answer, and nothing else. Never markup, never wrapped.
data: Final = Console(markup=False, highlight=False, soft_wrap=True)

#: Also the answer, when the answer is a table the operator reads.
data_table: Final = Console(highlight=False)


def print_header(title: str) -> None:
    """Announce what the command is about to do."""
    diagnostics.print(f"\n[bold cyan]{title}[/bold cyan]\n")


def print_success(message: str, details: Sequence[tuple[str, str]] = ()) -> None:
    """Report a clean outcome with optional key/value details."""
    body = f"[bold green]✅ {message}[/bold green]"
    if details:
        body += "\n\n" + "\n".join(
            f"[bold]{label}:[/bold] {value}" for label, value in details
        )
    diagnostics.print(Panel.fit(body, title="OK", border_style="green"))


def print_error(message: str, suggestion: str | None = None) -> None:
    """Report a failure, with the suggestion that resolves it."""
    diagnostics.print(f"[bold red]❌ Error:[/bold red] {message}")
    if suggestion:
        diagnostics.print(f"[yellow]💡 Suggestion:[/yellow] {suggestion}")


def print_defects(title: str, source: str, defects: Sequence[str]) -> None:
    """Report every defect found, one line each, so all can be fixed at once."""
    count = len(defects)
    noun = "defect" if count == 1 else "defects"
    body = "\n".join(f"[red]•[/red] {defect}" for defect in defects)
    diagnostics.print(
        Panel(
            body,
            title=f"[bold red]{title} — {count} {noun}[/bold red]",
            subtitle=f"[dim]{source}[/dim]",
            border_style="red",
        )
    )


def print_note(message: str) -> None:
    """Report something the operator should know but need not act on."""
    diagnostics.print(f"[yellow]⚠️  {message}[/yellow]")


def print_info(message: str) -> None:
    """Report progress or context."""
    diagnostics.print(f"[dim]{message}[/dim]")
