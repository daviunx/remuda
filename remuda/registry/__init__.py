"""Registry: named providers, models and pools, layered from config (FR-3)."""

from remuda.registry.errors import RegistryError, RegistryValidationError
from remuda.registry.loader import (
    CONFIG_DIR_ENV,
    MODELS_FILENAME,
    POOLS_FILENAME,
    PROJECT_CONFIG_DIRNAME,
    PROVIDERS_FILENAME,
    default_config_dirs,
    load_layer,
    load_registry,
)
from remuda.registry.models import (
    DiscoverQuery,
    DiscoverSort,
    ModelConfig,
    Pool,
    PoolEntry,
    PoolStrategy,
    Provider,
    ProviderKind,
)
from remuda.registry.registry import Registry

__all__ = [
    "CONFIG_DIR_ENV",
    "MODELS_FILENAME",
    "POOLS_FILENAME",
    "PROJECT_CONFIG_DIRNAME",
    "PROVIDERS_FILENAME",
    "DiscoverQuery",
    "DiscoverSort",
    "ModelConfig",
    "Pool",
    "PoolEntry",
    "PoolStrategy",
    "Provider",
    "ProviderKind",
    "Registry",
    "RegistryError",
    "RegistryValidationError",
    "default_config_dirs",
    "load_layer",
    "load_registry",
]
