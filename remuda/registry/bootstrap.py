"""Zero-config bootstrap: a usable registry with no files at all (FR-3b).

With `OPENROUTER_API_KEY` exported, or a local model server listening on its
conventional port, remuda configures itself. Nothing is magic: every implicit
provider and pool is announced, and an explicit registry entry of the same
name always wins.
"""

import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from remuda.registry.models import DiscoverQuery, ModelConfig, Pool, PoolEntry, Provider
from remuda.registry.registry import Registry

OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_PROVIDER = "openrouter"

#: Pool materialized from OpenRouter's free tier when nothing else is declared.
FREE_POOL = "free"

LOCAL_PROVIDER = "local"
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
LOCAL_BASE_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/v1"
LOCAL_POOL = "local"

#: Set to any non-empty value to skip bootstrapping entirely.
DISABLE_ENV = "REMUDA_NO_BOOTSTRAP"

#: How long the local probe waits before deciding nothing is listening.
PROBE_TIMEOUT_SECONDS = 0.25

#: Answers "is a local model server listening?" — injectable for tests.
LocalProbe = Callable[[], bool]


@dataclass(frozen=True)
class Bootstrap:
    """The implicit registry, and what to tell the operator about it."""

    registry: Registry = field(default_factory=Registry)
    announcements: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when nothing could be inferred from the environment."""
        return not self.registry.providers


def probe_local_endpoint(
    host: str = OLLAMA_HOST,
    port: int = OLLAMA_PORT,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Cheapest possible check: is anything accepting connections there?

    A TCP connect costs a round trip on loopback and tells us what we need.
    Asking the endpoint for its model list would cost a request on every
    single command.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def bootstrap_registry(
    environ: Mapping[str, str] | None = None,
    probe: LocalProbe | None = None,
) -> Bootstrap:
    """Infer providers and pools from the environment.

    Args:
        environ: the environment to read (defaults to the process's).
        probe: how to detect a local endpoint; None disables local detection.
    """
    variables = os.environ if environ is None else environ
    if variables.get(DISABLE_ENV):
        return Bootstrap()

    providers: list[Provider] = []
    models: list[ModelConfig] = []
    pools: list[Pool] = []
    announcements: list[str] = []

    if variables.get(OPENROUTER_KEY_ENV):
        providers.append(_openrouter_provider())
        pools.append(_free_pool())
        announcements.append(
            f"${OPENROUTER_KEY_ENV} is set — using an implicit '"
            f"{OPENROUTER_PROVIDER}' provider and a '{FREE_POOL}' pool "
            "discovered from its free tier"
        )

    if probe is not None and probe():
        providers.append(_local_provider())
        pools.append(_local_pool())
        announcements.append(
            f"a local model server answered on {OLLAMA_HOST}:{OLLAMA_PORT} — "
            f"using an implicit '{LOCAL_PROVIDER}' provider and a "
            f"'{LOCAL_POOL}' pool discovered from it"
        )

    return Bootstrap(
        registry=Registry(
            providers=providers, models=models, pools=pools, source="bootstrap"
        ),
        announcements=tuple(announcements),
    )


def with_bootstrap(explicit: Registry, bootstrap: Bootstrap) -> Registry:
    """Layer the explicit registry over the implicit one — explicit wins."""
    if bootstrap.is_empty:
        return explicit
    return Registry.layered(bootstrap.registry, explicit)


def _openrouter_provider() -> Provider:
    return Provider(
        name=OPENROUTER_PROVIDER,
        base_url=OPENROUTER_BASE_URL,
        key_env=OPENROUTER_KEY_ENV,
        catalog="openrouter",
    )


def _free_pool() -> Pool:
    return Pool(
        name=FREE_POOL,
        strategy="scatter",
        entries=(
            PoolEntry(
                discover=DiscoverQuery(
                    provider=OPENROUTER_PROVIDER,
                    free=True,
                    sort="throughput",
                    take=4,
                )
            ),
        ),
    )


def _local_provider() -> Provider:
    return Provider(name=LOCAL_PROVIDER, base_url=LOCAL_BASE_URL, catalog="ollama")


def _local_pool() -> Pool:
    return Pool(
        name=LOCAL_POOL,
        strategy="waterfall",
        entries=(PoolEntry(discover=DiscoverQuery(provider=LOCAL_PROVIDER, take=4)),),
    )
