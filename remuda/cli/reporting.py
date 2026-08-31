"""Rendering a run report for human eyes (FR-6)."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from remuda.ledger.models import Report


def print_report(report: Report, run_dir: Path | None, console: Console) -> None:
    """Print the outcome, the per-model evidence, and every failure."""
    console.print(_summary_panel(report, run_dir))
    if report.models:
        console.print(_models_table(report))
    for name, counts in report.distribution.items():
        rendered = " ".join(
            f"{label}={count}" for label, count in sorted(counts.items())
        )
        console.print(f"[dim]{name}: {rendered}[/dim]")
    _print_problems(report, console)


def _summary_panel(report: Report, run_dir: Path | None) -> Panel:
    verdict = (
        "[bold green]✅ Run complete[/bold green]"
        if report.is_successful
        else f"[bold red]❌ {report.failed} key(s) failed[/bold red]"
    )
    body = (
        f"{verdict}\n\n"
        f"[bold]Job:[/bold] {report.job_name}\n"
        f"[bold]Rows:[/bold] {report.rows}\n"
        f"[bold]Results:[/bold] ok {report.ok} · skipped {report.skipped} · "
        f"failed {report.failed}\n"
        f"[bold]Cost:[/bold] ${report.total_cost_usd:.4f}"
    )
    if report.resumed_results:
        body += f"\n[bold]Reused from the previous run:[/bold] {report.resumed_results}"
    if run_dir is not None:
        body += f"\n[bold]Run:[/bold] {run_dir}"
    return Panel.fit(
        body,
        title="Report",
        border_style="green" if report.is_successful else "red",
    )


def _models_table(report: Report) -> Table:
    table = Table(title="Models", show_header=True, header_style="bold cyan")
    table.add_column("Model", style="green")
    table.add_column("Answers", justify="right")
    table.add_column("Rejects", justify="right")
    table.add_column("Transient", justify="right")
    table.add_column("Avg ms", justify="right")
    table.add_column("Cost", justify="right")
    for name, stats in report.models.items():
        table.add_row(
            name,
            str(stats.answers),
            str(stats.rejects),
            str(stats.transient_failures),
            f"{stats.average_latency_ms:.0f}",
            f"${stats.cost_usd:.4f}",
        )
    return table


def _print_problems(report: Report, console: Console) -> None:
    for entry in report.skips[:20]:
        console.print(
            f"[yellow]skipped[/yellow] {entry.key} · {entry.field}: {entry.reason}"
        )
    for entry in report.failures[:20]:
        console.print(
            f"[red]failed[/red] {entry.key} · {entry.field} "
            f"(after {entry.attempts} attempt(s)): {entry.reason}"
        )
    hidden = max(0, len(report.failures) - 20) + max(0, len(report.skips) - 20)
    if hidden:
        console.print(f"[dim]… and {hidden} more in the run's report.json[/dim]")
