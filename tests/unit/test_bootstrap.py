"""FR-3b — zero-config bootstrap: inferred, announced, always overridable."""

from remuda.catalog.models import FULL_CAPABILITIES, CatalogModel
from remuda.catalog.resolve import apply_query
from remuda.registry.bootstrap import (
    DISABLE_ENV,
    FREE_POOL,
    LOCAL_POOL,
    LOCAL_PROVIDER,
    OPENROUTER_KEY_ENV,
    OPENROUTER_PROVIDER,
    bootstrap_registry,
    with_bootstrap,
)
from remuda.registry.models import Pool, PoolEntry, Provider
from remuda.registry.registry import Registry

WITH_KEY: dict[str, str] = {OPENROUTER_KEY_ENV: "sk-test"}
NOTHING: dict[str, str] = {}


def no_local() -> bool:
    return False


def local_answers() -> bool:
    return True


class TestInference:
    def test_an_openrouter_key_implies_a_provider_and_a_free_pool(self) -> None:
        result = bootstrap_registry(environ=WITH_KEY, probe=no_local)

        assert OPENROUTER_PROVIDER in result.registry.providers
        assert FREE_POOL in result.registry.pools
        provider = result.registry.provider(OPENROUTER_PROVIDER)
        assert provider.key_env == OPENROUTER_KEY_ENV
        assert provider.catalog == "openrouter"

    def test_the_free_pool_is_a_discovery_query_not_a_hardcoded_list(self) -> None:
        result = bootstrap_registry(environ=WITH_KEY, probe=no_local)

        pool = result.registry.pool(FREE_POOL)
        assert pool.model_names == ()
        query = pool.discover_queries[0]
        assert query.free is True
        assert query.take == 4

    def test_the_free_pool_query_is_answerable_by_a_live_shaped_catalog(self) -> None:
        """OpenRouter's live model list carries pricing and context but NO
        throughput stats — the implicit pool must only ask for what the
        catalog can answer, or zero-config refuses on its flagship path."""
        result = bootstrap_registry(environ=WITH_KEY, probe=no_local)

        query = result.registry.pool(FREE_POOL).discover_queries[0]
        live_shaped = [
            CatalogModel(
                id=f"m{i}",
                prompt_price_usd=0.0,
                completion_price_usd=0.0,
                context_length=1000 * (i + 1),
            )
            for i in range(5)
        ]
        selected = apply_query(
            query,
            live_shaped,
            FULL_CAPABILITIES,
            provider="openrouter",
            catalog="openrouter",
        )
        assert len(selected) == query.take

    def test_a_reachable_local_endpoint_implies_a_local_provider(self) -> None:
        result = bootstrap_registry(environ=NOTHING, probe=local_answers)

        assert LOCAL_PROVIDER in result.registry.providers
        assert result.registry.provider(LOCAL_PROVIDER).catalog == "ollama"
        assert LOCAL_POOL in result.registry.pools

    def test_nothing_is_inferred_from_an_empty_environment(self) -> None:
        result = bootstrap_registry(environ=NOTHING, probe=no_local)

        assert result.is_empty
        assert result.announcements == ()

    def test_the_local_probe_is_skipped_when_none_is_given(self) -> None:
        result = bootstrap_registry(environ=NOTHING, probe=None)

        assert result.is_empty

    def test_both_sources_can_be_inferred_at_once(self) -> None:
        result = bootstrap_registry(environ=WITH_KEY, probe=local_answers)

        assert set(result.registry.pools) == {FREE_POOL, LOCAL_POOL}


class TestAnnouncements:
    def test_every_implicit_resolution_is_announced(self) -> None:
        result = bootstrap_registry(environ=WITH_KEY, probe=local_answers)

        assert len(result.announcements) == 2
        assert any(OPENROUTER_KEY_ENV in note for note in result.announcements)
        assert any("local model server" in note for note in result.announcements)

    def test_the_announcement_names_what_it_created(self) -> None:
        note = bootstrap_registry(environ=WITH_KEY, probe=no_local).announcements[0]

        assert OPENROUTER_PROVIDER in note
        assert FREE_POOL in note


class TestOptOut:
    def test_the_disable_variable_stops_all_inference(self) -> None:
        result = bootstrap_registry(
            environ={**WITH_KEY, DISABLE_ENV: "1"}, probe=local_answers
        )

        assert result.is_empty
        assert result.announcements == ()


class TestPrecedence:
    def test_an_explicit_provider_of_the_same_name_wins(self) -> None:
        inferred = bootstrap_registry(environ=WITH_KEY, probe=no_local)
        explicit = Registry(
            providers=[
                Provider(
                    name=OPENROUTER_PROVIDER,
                    base_url="https://proxy.internal/v1",
                    key_env="MY_KEY",
                )
            ],
            source="project",
        )

        merged = with_bootstrap(explicit, inferred)

        assert merged.provider(OPENROUTER_PROVIDER).base_url == (
            "https://proxy.internal/v1"
        )

    def test_an_explicit_pool_of_the_same_name_wins(self) -> None:
        inferred = bootstrap_registry(environ=WITH_KEY, probe=no_local)
        explicit = Registry(
            providers=[Provider(name="p", base_url="http://x/v1")],
            pools=[Pool(name=FREE_POOL, entries=(PoolEntry(model="mine"),))],
        )

        merged = with_bootstrap(explicit, inferred)

        assert merged.pool(FREE_POOL).model_names == ("mine",)

    def test_implicit_entries_survive_alongside_unrelated_explicit_ones(self) -> None:
        inferred = bootstrap_registry(environ=WITH_KEY, probe=no_local)
        explicit = Registry(providers=[Provider(name="other", base_url="http://x/v1")])

        merged = with_bootstrap(explicit, inferred)

        assert set(merged.providers) == {OPENROUTER_PROVIDER, "other"}

    def test_an_empty_bootstrap_returns_the_explicit_registry_untouched(self) -> None:
        explicit = Registry(providers=[Provider(name="p", base_url="http://x/v1")])

        merged = with_bootstrap(
            explicit, bootstrap_registry(environ=NOTHING, probe=no_local)
        )

        assert merged is explicit
