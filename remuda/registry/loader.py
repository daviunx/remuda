"""Layered configuration files for the registry (FR-3).

User-level configuration (`~/.config/remuda/`) is overridden by project-level
configuration (`.remuda/`), name by name. Files are one loader over the
registry — never the only way to build one.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from remuda.registry.errors import RegistryValidationError
from remuda.registry.models import ModelConfig, Pool, PoolEntry, Provider
from remuda.registry.registry import Registry

PROVIDERS_FILENAME = "providers.yaml"
MODELS_FILENAME = "models.yaml"
POOLS_FILENAME = "pools.yaml"

#: Overrides the user-level configuration directory.
CONFIG_DIR_ENV = "REMUDA_CONFIG_DIR"

#: Project-level configuration directory, relative to the project root.
PROJECT_CONFIG_DIRNAME = ".remuda"


def default_config_dirs(project_root: Path | None = None) -> tuple[Path, ...]:
    """Return the configuration directories, in layering order."""
    override = os.environ.get(CONFIG_DIR_ENV)
    user_dir = Path(override) if override else Path.home() / ".config" / "remuda"
    project_dir = (project_root or Path.cwd()) / PROJECT_CONFIG_DIRNAME
    return (user_dir, project_dir)


def load_registry(config_dirs: Sequence[Path]) -> Registry:
    """Load and merge every layer, then check the merged registry.

    Raises:
        RegistryValidationError: a layer is malformed, or the merged
            registry holds a dangling reference.
    """
    merged = Registry.layered(*(load_layer(directory) for directory in config_dirs))
    merged.validate_or_raise()
    return merged


def load_layer(directory: Path) -> Registry:
    """Load one configuration directory. A missing directory is an empty layer.

    Raises:
        RegistryValidationError: a file in the directory is malformed.
    """
    return Registry(
        providers=[
            _build(Provider, name, body, directory / PROVIDERS_FILENAME)
            for name, body in _entries(directory / PROVIDERS_FILENAME).items()
        ],
        models=[
            _build(ModelConfig, name, body, directory / MODELS_FILENAME)
            for name, body in _entries(directory / MODELS_FILENAME).items()
        ],
        pools=[
            _build(Pool, name, _normalize_pool(body), directory / POOLS_FILENAME)
            for name, body in _entries(directory / POOLS_FILENAME).items()
        ],
        source=str(directory),
    )


def _entries(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise RegistryValidationError(
            str(path), [f"YAML is not parseable: {error}"]
        ) from error
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise RegistryValidationError(
            str(path),
            [
                "file must contain a mapping of name to declaration, found "
                f"{type(parsed).__name__}"
            ],
        )
    return {str(name): body for name, body in parsed.items()}


def _normalize_pool(body: Any) -> Any:
    """Accept the shorthand spellings of a pool declaration."""
    if isinstance(body, list):
        body = {"models": body}
    if not isinstance(body, dict):
        return body
    normalized = dict(body)
    shorthand = normalized.pop("models", None)
    if shorthand is None:
        return normalized
    if not isinstance(shorthand, list):
        return body
    entries = [PoolEntry(model=str(name)) for name in shorthand]
    entries.extend(_as_entries(normalized.pop("entries", None)))
    normalized["entries"] = entries
    return normalized


def _as_entries(declared: Any) -> list[Any]:
    if declared is None:
        return []
    if isinstance(declared, list):
        return list(declared)
    return [declared]


def _build[T: Provider | ModelConfig | Pool](
    model: type[T], name: str, body: Any, path: Path
) -> T:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise RegistryValidationError(
            str(path),
            [f"'{name}' must be a mapping, found {type(body).__name__}"],
        )
    if "name" in body and body["name"] != name:
        raise RegistryValidationError(
            str(path),
            [
                f"'{name}' declares a conflicting name '{body['name']}' — the "
                "key IS the name"
            ],
        )
    try:
        # cast: model_validate on a union-bounded type[T] widens to the bound.
        return cast("T", model.model_validate({**body, "name": name}))
    except ValidationError as error:
        raise RegistryValidationError(str(path), _describe(name, error)) from error


def _describe(name: str, error: ValidationError) -> list[str]:
    defects: list[str] = []
    for item in error.errors():
        message = str(item["msg"]).removeprefix("Value error, ")
        location = ".".join(str(part) for part in item["loc"])
        if message.startswith(("provider '", "pool '", "model '")):
            defects.append(message)
        elif location:
            defects.append(f"'{name}' → {location}: {message}")
        else:
            defects.append(f"'{name}': {message}")
    return sorted(set(defects))
