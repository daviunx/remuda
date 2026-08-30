"""The registry: named providers, models and pools, layered (FR-3).

The registry is constructible entirely in code — configuration files are one
loader on top of it, never the only way in (FR-7).
"""

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from remuda.registry.errors import RegistryError, RegistryValidationError
from remuda.registry.models import ModelConfig, Pool, Provider
from remuda.spec.models import Job


class Registry:
    """Named providers, models and pools, with reference checking."""

    def __init__(
        self,
        providers: Iterable[Provider] = (),
        models: Iterable[ModelConfig] = (),
        pools: Iterable[Pool] = (),
        source: str = "programmatic",
    ) -> None:
        self.source = source
        self._providers = _by_name(providers, "provider", source)
        self._models = _by_name(models, "model", source)
        self._pools = _by_name(pools, "pool", source)

    # -- layering ----------------------------------------------------------

    @classmethod
    def layered(cls, *layers: "Registry") -> "Registry":
        """Merge layers left to right; a later layer overrides by name."""
        providers: dict[str, Provider] = {}
        models: dict[str, ModelConfig] = {}
        pools: dict[str, Pool] = {}
        sources: list[str] = []
        for layer in layers:
            providers.update(layer.providers)
            models.update(layer.models)
            pools.update(layer.pools)
            sources.append(layer.source)
        return cls(
            providers=providers.values(),
            models=models.values(),
            pools=pools.values(),
            source=" → ".join(sources) if sources else "empty",
        )

    # -- lookups -----------------------------------------------------------

    @property
    def providers(self) -> Mapping[str, Provider]:
        """Every registered provider, by name."""
        return MappingProxyType(self._providers)

    @property
    def models(self) -> Mapping[str, ModelConfig]:
        """Every registered model, by name."""
        return MappingProxyType(self._models)

    @property
    def pools(self) -> Mapping[str, Pool]:
        """Every registered pool, by name."""
        return MappingProxyType(self._pools)

    def provider(self, name: str) -> Provider:
        """Return the provider registered under `name`.

        Raises:
            RegistryError: no such provider is registered.
        """
        return _require(self._providers, name, "provider", self.source)

    def model(self, name: str) -> ModelConfig:
        """Return the model registered under `name`.

        Raises:
            RegistryError: no such model is registered.
        """
        return _require(self._models, name, "model", self.source)

    def pool(self, name: str) -> Pool:
        """Return the pool registered under `name`.

        Raises:
            RegistryError: no such pool is registered.
        """
        return _require(self._pools, name, "pool", self.source)

    # -- validation --------------------------------------------------------

    def validate(self) -> list[str]:
        """Return one defect line per dangling reference in the registry."""
        defects: list[str] = []
        for model in self._models.values():
            if model.provider not in self._providers:
                defects.append(
                    f"model '{model.name}': unknown provider "
                    f"'{model.provider}' ({_known(self._providers, 'provider')})"
                )
        for pool in self._pools.values():
            defects.extend(self._pool_defects(pool))
        return defects

    def validate_or_raise(self) -> None:
        """Raise when the registry holds a dangling reference.

        Raises:
            RegistryValidationError: at least one reference is dangling.
        """
        defects = self.validate()
        if defects:
            raise RegistryValidationError(self.source, defects)

    def check_job(self, job: Job) -> list[str]:
        """Return one defect line per pool/model/provider the job cannot reach.

        This is the half of the FR-1 lint that needs the registry to resolve
        names — `Job.from_dir()` cannot answer it on its own.
        """
        defects: list[str] = []
        for field in job.fields:
            if field.pool is None:
                continue
            pool = self._pools.get(field.pool)
            if pool is None:
                defects.append(
                    f"field '{field.name}': unknown pool '{field.pool}' "
                    f"({_known(self._pools, 'pool')})"
                )
                continue
            defects.extend(
                f"field '{field.name}': {defect}" for defect in self._pool_defects(pool)
            )
        return defects

    def _pool_defects(self, pool: Pool) -> list[str]:
        defects: list[str] = []
        referenced = list(pool.model_names)
        if pool.mopup is not None:
            referenced.append(pool.mopup)
        for model_name in referenced:
            model = self._models.get(model_name)
            if model is None:
                defects.append(
                    f"pool '{pool.name}' references unknown model "
                    f"'{model_name}' ({_known(self._models, 'model')})"
                )
            elif model.provider not in self._providers:
                defects.append(
                    f"pool '{pool.name}' → model '{model_name}' references "
                    f"unknown provider '{model.provider}' "
                    f"({_known(self._providers, 'provider')})"
                )
        for query in pool.discover_queries:
            if query.provider not in self._providers:
                defects.append(
                    f"pool '{pool.name}' discover query references unknown "
                    f"provider '{query.provider}' "
                    f"({_known(self._providers, 'provider')})"
                )
        return defects


def _by_name[T: Provider | ModelConfig | Pool](
    entries: Iterable[T], noun: str, source: str
) -> dict[str, T]:
    registered: dict[str, T] = {}
    for entry in entries:
        if entry.name in registered:
            raise RegistryValidationError(
                source, [f"{noun} '{entry.name}' is declared twice"]
            )
        registered[entry.name] = entry
    return registered


def _require[T](registered: Mapping[str, T], name: str, noun: str, source: str) -> T:
    try:
        return registered[name]
    except KeyError:
        raise RegistryError(
            f"unknown {noun} '{name}' in registry ({source}) — "
            f"{_known(registered, noun)}"
        ) from None


def _known(registered: Mapping[str, object], noun: str) -> str:
    if not registered:
        return f"no {noun}s are registered"
    return f"registered {noun}s: {', '.join(sorted(registered))}"
