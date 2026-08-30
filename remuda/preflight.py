"""Checks a run performs before it starts calling models (FR-3).

The opencode transport shells out to a CLI whose output framing is not a
stable contract. Probing its version once, up front, means a version that
changed under us is reported as a version — not as a run's worth of
unparseable answers.
"""

from collections.abc import Iterable

from remuda.catalog.resolve import ResolvedPool
from remuda.registry.registry import Registry
from remuda.transport.opencode import OpencodeTransport


async def preflight_opencode(
    registry: Registry, resolved: Iterable[ResolvedPool]
) -> list[str]:
    """Probe every opencode provider a run will actually use.

    Returns one announcement per probed provider.

    Raises:
        TransportConfigurationError: the executable is missing.
        TransientTransportError: the probe failed.
    """
    announcements: list[str] = []
    for provider_name in _opencode_providers(registry, resolved):
        provider = registry.provider(provider_name)
        transport = OpencodeTransport(provider)
        version = await transport.probe_version()
        announcements.append(
            f"provider '{provider.name}' runs '{transport.command}' ({version}) — "
            "this run is laptop-only"
        )
    return announcements


def _opencode_providers(
    registry: Registry, resolved: Iterable[ResolvedPool]
) -> list[str]:
    names: list[str] = []
    for pool in resolved:
        members = [*pool.members, *([pool.mopup] if pool.mopup else [])]
        for member in members:
            provider = registry.providers.get(member.provider)
            if (
                provider is not None
                and provider.kind == "opencode"
                and provider.name not in names
            ):
                names.append(provider.name)
    return names
