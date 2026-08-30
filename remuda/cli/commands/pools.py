"""`remuda pools show` and `remuda pools check` (FR-3, FR-12)."""

import asyncio
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.table import Table

from remuda.catalog.errors import CatalogError
from remuda.catalog.resolve import ResolvedMember, ResolvedPool, resolve_pool
from remuda.cli.console import (
    EXIT_ERROR,
    data_table,
    print_error,
    print_info,
)
from remuda.cli.options import ConfigDirs, NoBootstrap, resolve_registry
from remuda.health import MemberHealth, alive_count, probe_pool
from remuda.registry.errors import RegistryError, RegistryValidationError
from remuda.registry.registry import Registry

pools_app = typer.Typer(
    name="pools",
    help="Inspect and probe model pools.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@pools_app.command("show")
def show(
    name: Annotated[str, typer.Argument(help="Pool name.")],
    *,
    config_dir: ConfigDirs = None,
    no_bootstrap: NoBootstrap = False,
) -> None:
    """
    Print a pool's materialized membership.

    A pool whose members come from a [cyan]discover:[/cyan] query is resolved
    against the provider's live catalog, so what you see is what a run
    starting now would use.

    [bold cyan]Examples:[/bold cyan]

        remuda pools show free-fast

        remuda pools show free --no-bootstrap
    """
    registry = _registry(config_dir, should_bootstrap=not no_bootstrap)
    resolved = _resolve(registry, name)
    data_table.print(_membership_table(resolved))


@pools_app.command("check")
def check(
    name: Annotated[str, typer.Argument(help="Pool name.")],
    *,
    config_dir: ConfigDirs = None,
    no_bootstrap: NoBootstrap = False,
) -> None:
    """
    Probe every member of a pool with one minimal request.

    Reports alive, latency and refusal per member. It writes nothing — no run
    directory, no ledger — and always exits 0: a probe reports, it does not
    judge which models you should keep.

    [bold cyan]Examples:[/bold cyan]

        remuda pools check free-fast

        remuda pools check local -c ./.remuda
    """
    registry = _registry(config_dir, should_bootstrap=not no_bootstrap)
    try:
        resolved, results = asyncio.run(_probe(registry, name))
    except (RegistryError, CatalogError) as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error

    data_table.print(_health_table(resolved, results))
    print_info(f"{alive_count(results)}/{len(results)} member(s) answered")


async def _probe(
    registry: Registry, name: str
) -> tuple[ResolvedPool, list[MemberHealth]]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await probe_pool(name, registry, client)


def _registry(config_dir: list[Path] | None, should_bootstrap: bool) -> Registry:
    try:
        return resolve_registry(config_dir, should_bootstrap=should_bootstrap)
    except RegistryValidationError as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error


def _resolve(registry: Registry, name: str) -> ResolvedPool:
    async def run() -> ResolvedPool:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await resolve_pool(registry.pool(name), registry, client)

    try:
        return asyncio.run(run())
    except (RegistryError, CatalogError) as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error


def _membership_table(resolved: ResolvedPool) -> Table:
    table = Table(
        title=f"Pool '{resolved.pool}' ({resolved.strategy})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Model", style="green")
    table.add_column("Provider", style="blue")
    table.add_column("Source")
    table.add_column("Context", justify="right")
    table.add_column("Prompt $/tok", justify="right")
    for position, member in enumerate(resolved.members, start=1):
        table.add_row(*_membership_row(str(position), member))
    if resolved.mopup is not None:
        table.add_row(*_membership_row("mop-up", resolved.mopup))
    return table


def _membership_row(position: str, member: ResolvedMember) -> tuple[str, ...]:
    return (
        position,
        member.name,
        member.provider,
        member.source,
        str(member.context_length or "—"),
        "free" if member.prompt_price_usd == 0 else str(member.prompt_price_usd or "—"),
    )


def _health_table(resolved: ResolvedPool, results: list[MemberHealth]) -> Table:
    table = Table(
        title=f"Pool '{resolved.pool}' health",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model", style="green")
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")
    table.add_column("Detail")
    for result in results:
        table.add_row(
            result.name,
            "[green]✓ alive[/green]" if result.is_alive else "[red]✗ failed[/red]",
            f"{result.latency_ms:.0f} ms",
            result.detail,
        )
    return table
