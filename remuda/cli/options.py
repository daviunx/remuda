"""Options and registry resolution shared by several commands."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from remuda.cli.console import print_info
from remuda.registry.bootstrap import (
    bootstrap_registry,
    probe_local_endpoint,
    with_bootstrap,
)
from remuda.registry.loader import default_config_dirs, load_registry
from remuda.registry.registry import Registry

#: The registry flags every command that resolves one accepts. Declared once so
#: the flag, its shorthand and its help read identically everywhere, and so a
#: command's signature stays a list of names rather than a wall of declarations.
ConfigDirs = Annotated[
    list[Path] | None,
    typer.Option(
        "--config-dir",
        "-c",
        help=(
            "Registry directory. Repeatable — later layers override earlier "
            "ones. Defaults to the user and project directories."
        ),
    ),
]
NoBootstrap = Annotated[
    bool,
    typer.Option("--no-bootstrap", help="Ignore providers implied by the environment."),
]


def resolve_config_dirs(declared: Sequence[Path] | None) -> tuple[Path, ...]:
    """Return the registry layers to load, in order.

    Explicit `--config-dir` layers replace the defaults entirely, so a run can
    be pinned to a known registry without the operator's own config leaking in.
    """
    if declared:
        return tuple(Path(path) for path in declared)
    return default_config_dirs()


def resolve_registry(
    declared: Sequence[Path] | None, should_bootstrap: bool = True
) -> Registry:
    """Load the configured registry, layered over what the environment implies.

    Every implicit provider or pool is announced on stderr, and an explicit
    entry of the same name always overrides it (FR-3b).

    Raises:
        RegistryValidationError: a layer is malformed, or the merged registry
            holds a dangling reference.
    """
    config_dirs = resolve_config_dirs(declared)
    print_info("registry layers: " + ", ".join(str(path) for path in config_dirs))
    # Validated only after the bootstrap layer is merged in: an explicit
    # pool may legitimately name a model the environment provides.
    explicit = load_registry(config_dirs, should_validate=not should_bootstrap)
    if not should_bootstrap:
        return explicit

    inferred = bootstrap_registry(probe=probe_local_endpoint)
    for announcement in inferred.announcements:
        print_info(announcement)
    merged = with_bootstrap(explicit, inferred)
    merged.validate_or_raise()
    return merged
