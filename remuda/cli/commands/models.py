"""`remuda models list` — what a provider currently serves (FR-3, FR-8)."""

import asyncio
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.table import Table

from remuda.catalog.adapters import adapter_for
from remuda.catalog.errors import CatalogError
from remuda.catalog.models import CatalogModel
from remuda.cli.console import EXIT_ERROR, data_table, print_error, print_info
from remuda.cli.options import resolve_registry
from remuda.registry.errors import RegistryError, RegistryValidationError
from remuda.registry.registry import Registry

models_app = typer.Typer(
    name="models",
    help="Inspect the models a provider serves.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@models_app.command("list")
def list_models(
    provider: Annotated[str, typer.Argument(help="Registered provider name.")],
    *,
    free: Annotated[
        bool, typer.Option("--free", help="Only models the catalog prices at zero.")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", "-l", min=1, help="Show at most N.")
    ] = None,
    config_dir: Annotated[
        list[Path] | None,
        typer.Option("--config-dir", "-c", help="Registry layer. Repeatable."),
    ] = None,
    no_bootstrap: Annotated[
        bool,
        typer.Option(
            "--no-bootstrap", help="Ignore providers implied by the environment."
        ),
    ] = False,
) -> None:
    """
    List the models a provider currently serves.

    Reads the provider's own catalog. A catalog that reports only names shows
    dashes for context and price rather than inventing them.

    [bold cyan]Examples:[/bold cyan]

        remuda models list openrouter --free --limit 20

        remuda models list local
    """
    try:
        registry = resolve_registry(config_dir, bootstrap=not no_bootstrap)
        catalog = asyncio.run(_fetch(registry, provider))
    except (RegistryError, RegistryValidationError, CatalogError) as error:
        print_error(str(error))
        raise typer.Exit(code=EXIT_ERROR) from error

    selected = [model for model in catalog if model.is_free] if free else list(catalog)
    if limit is not None:
        selected = selected[:limit]
    data_table.print(_table(provider, selected))
    print_info(f"{len(selected)} of {len(catalog)} model(s) shown")


async def _fetch(registry: Registry, provider_name: str) -> list[CatalogModel]:
    declared = registry.provider(provider_name)
    adapter = adapter_for(declared)
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await adapter.fetch(declared, client)


def _table(provider: str, models: list[CatalogModel]) -> Table:
    table = Table(
        title=f"Models served by '{provider}'",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model", style="green")
    table.add_column("Context", justify="right")
    table.add_column("Prompt $/tok", justify="right")
    table.add_column("Completion $/tok", justify="right")
    for model in models:
        table.add_row(
            model.id,
            str(model.context_length or "—"),
            _price(model.prompt_price_usd),
            _price(model.completion_price_usd),
        )
    return table


def _price(value: float | None) -> str:
    if value is None:
        return "—"
    return "free" if value == 0 else f"{value:.8f}".rstrip("0")
