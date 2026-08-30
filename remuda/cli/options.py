"""Options shared by several commands."""

from collections.abc import Sequence
from pathlib import Path

from remuda.registry.loader import default_config_dirs


def resolve_config_dirs(declared: Sequence[Path] | None) -> tuple[Path, ...]:
    """Return the registry layers to load, in order.

    Explicit `--config-dir` layers replace the defaults entirely, so a run can
    be pinned to a known registry without the operator's own config leaking in.
    """
    if declared:
        return tuple(Path(path) for path in declared)
    return default_config_dirs()
