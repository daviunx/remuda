"""`remuda stats` and `remuda runs` — evidence over past runs (FR-14)."""

import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from remuda.cli.console import (
    EXIT_ERROR,
    data_table,
    print_error,
    print_info,
    print_note,
)
from remuda.ledger.store import RUNS_DIRNAME
from remuda.stats import (
    ModelAggregate,
    RunSummary,
    aggregate_models,
    collect_runs,
    prunable_runs,
)

runs_app = typer.Typer(
    name="runs",
    help="List and prune past runs.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

RunsDir = Annotated[
    Path,
    typer.Option(
        "--runs-dir",
        help="Where run directories live (a job's are under its own directory).",
    ),
]
JobFilter = Annotated[
    str | None,
    typer.Option("--job", "-j", help="Only this job's runs."),
]


def stats(
    *,
    runs_dir: RunsDir = Path(RUNS_DIRNAME),
    job: JobFilter = None,
) -> None:
    """
    Rank models by how they have actually performed across past runs.

    Reads the persisted run reports only — no model is called, nothing is
    sent anywhere. These numbers are for YOU to order a pool with: remuda
    never reorders a pool from its own statistics.

    [bold cyan]Examples:[/bold cyan]

        remuda stats --runs-dir jobs/rally-severity/.runs

        remuda stats --job rally-severity
    """
    summaries = collect_runs(runs_dir, job)
    if not summaries:
        print_error(
            f"no finished runs under {runs_dir}",
            suggestion="Point --runs-dir at a job's .runs directory.",
        )
        raise typer.Exit(code=EXIT_ERROR)

    models = aggregate_models(summaries)
    data_table.print(_stats_table(models, len(summaries)))
    print_info(f"{len(models)} model(s) across {len(summaries)} run(s)")
    print_note("these numbers inform your pool order — remuda never reorders a pool")


@runs_app.command("list")
def list_runs_command(
    *,
    runs_dir: RunsDir = Path(RUNS_DIRNAME),
    job: JobFilter = None,
) -> None:
    """
    List past runs, newest first.

    [bold cyan]Examples:[/bold cyan]

        remuda runs list --runs-dir jobs/rally-severity/.runs
    """
    summaries = collect_runs(runs_dir, job)
    if not summaries:
        print_error(f"no finished runs under {runs_dir}")
        raise typer.Exit(code=EXIT_ERROR)
    data_table.print(_runs_table(summaries))
    print_info(f"{len(summaries)} run(s)")


@runs_app.command("prune")
def prune_command(
    *,
    keep: Annotated[
        int,
        typer.Option("--keep", "-k", min=1, help="Runs to keep per job."),
    ] = 5,
    runs_dir: RunsDir = Path(RUNS_DIRNAME),
    job: JobFilter = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--write",
            "-d/-W",
            help="Dry by default — pass --write to actually delete.",
        ),
    ] = True,
) -> None:
    """
    Remove old run directories, keeping the newest N of each job.

    Dry by default: it prints exactly what a live run would delete. Pass
    [cyan]--write[/cyan] to actually remove them. Deleting a run discards its
    ledger, so `render` and `report` can no longer read it.

    [bold cyan]Examples:[/bold cyan]

        # See what would go
        remuda runs prune --keep 3 --runs-dir jobs/my-job/.runs

        # Actually remove them
        remuda runs prune --keep 3 --runs-dir jobs/my-job/.runs --write
    """
    removable = prunable_runs(collect_runs(runs_dir, job), keep=keep)
    if not removable:
        print_info(f"nothing to prune — every job has at most {keep} run(s)")
        return

    for summary in removable:
        data_table.print(f"{summary.job_name}  {summary.run_id}  {summary.path}")
    if dry_run:
        print_note(
            f"dry run — {len(removable)} run(s) would be removed. "
            "Re-run with --write to delete them."
        )
        return

    for summary in removable:
        shutil.rmtree(summary.path)
    print_info(f"removed {len(removable)} run(s)")


def _stats_table(models: list[ModelAggregate], runs: int) -> Table:
    table = Table(
        title=f"Model performance across {runs} run(s)",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model", style="green")
    table.add_column("Success", justify="right")
    table.add_column("Answers", justify="right")
    table.add_column("Rejected", justify="right")
    table.add_column("Transient", justify="right")
    table.add_column("Avg ms", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Runs", justify="right")
    table.add_column("Last seen")
    for model in models:
        table.add_row(
            model.name,
            f"{model.success_rate:.0%}",
            str(model.answers),
            str(model.rejects),
            str(model.transient_failures),
            f"{model.average_latency_ms:.0f}",
            f"${model.cost_usd:.4f}",
            str(model.runs),
            model.last_seen.strftime("%Y-%m-%d") if model.last_seen else "—",
        )
    return table


def _runs_table(summaries: list[RunSummary]) -> Table:
    table = Table(title="Runs", show_header=True, header_style="bold cyan")
    table.add_column("Job", style="green")
    table.add_column("Run", style="blue")
    table.add_column("When")
    table.add_column("Rows", justify="right")
    table.add_column("OK", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Failed", justify="right")
    table.add_column("Cost", justify="right")
    for summary in summaries:
        table.add_row(
            summary.job_name,
            summary.run_id,
            summary.started_at.strftime("%Y-%m-%d %H:%M"),
            str(summary.rows),
            str(summary.ok),
            str(summary.skipped),
            f"[red]{summary.failed}[/red]" if summary.failed else "0",
            f"${summary.cost_usd:.4f}",
        )
    return table
