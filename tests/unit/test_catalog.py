"""FR-3 — catalog adapters, and the filters each one can and cannot answer.

Adapters are read against RECORDED fixtures of each provider's real response
shape, because catalog drift is the failure mode this design is exposed to
(analysis.md watchlist).
"""

import json
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from remuda.catalog.adapters import (
    OllamaCatalog,
    OpenAICompatCatalog,
    OpenRouterCatalog,
    adapter_for,
)
from remuda.catalog.errors import CatalogError, UnsupportedFilterError
from remuda.catalog.models import CatalogModel
from remuda.catalog.resolve import (
    ResolvedMember,
    ResolvedPool,
    apply_query,
    member_to_model,
    requested_filters,
    resolve_pool,
    to_model_configs,
)
from remuda.registry.models import DiscoverQuery, ModelConfig, Pool, PoolEntry, Provider
from remuda.registry.registry import Registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def client_serving(payload: Any, expect_path: str | None = None) -> httpx.AsyncClient:
    """An httpx client that answers any GET with the recorded payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        if expect_path is not None:
            assert request.url.path == expect_path, request.url.path
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def openrouter_provider() -> Provider:
    return Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        catalog="openrouter",
    )


class TestOpenRouterAdapter:
    async def test_recorded_catalog_is_parsed(self) -> None:
        async with client_serving(
            fixture("openrouter_models.json"), "/api/v1/models"
        ) as client:
            models = await OpenRouterCatalog().fetch(openrouter_provider(), client)

        assert len(models) == 5
        first = models[0]
        assert first.id == "meta-llama/llama-3.3-70b-instruct:free"
        assert first.context_length == 65536
        assert first.prompt_price_usd == 0
        assert first.throughput == 92.4
        assert first.is_free

    async def test_priced_models_are_not_free(self) -> None:
        async with client_serving(fixture("openrouter_models.json")) as client:
            models = await OpenRouterCatalog().fetch(openrouter_provider(), client)

        priced = next(model for model in models if model.id == "openai/gpt-4o-mini")
        assert priced.is_free is False
        assert priced.prompt_price_usd == 0.00000015

    async def test_a_catalog_error_status_is_reported(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(CatalogError, match="503"):
                await OpenRouterCatalog().fetch(openrouter_provider(), client)

    async def test_an_unreachable_catalog_is_reported(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(CatalogError, match="cannot reach"):
                await OpenRouterCatalog().fetch(openrouter_provider(), client)


class TestNameOnlyAdapters:
    async def test_openai_compatible_list_yields_ids(self) -> None:
        provider = Provider(
            name="vllm", base_url="http://localhost:8000/v1", catalog="openai_compat"
        )

        async with client_serving(
            fixture("openai_models.json"), "/v1/models"
        ) as client:
            models = await OpenAICompatCatalog().fetch(provider, client)

        assert [model.id for model in models] == ["qwen3-4b", "mistral-7b-instruct"]
        assert models[0].context_length is None
        assert models[0].has_pricing is False

    async def test_ollama_tags_are_read_from_the_native_path(self) -> None:
        provider = Provider(
            name="local", base_url="http://localhost:11434/v1", catalog="ollama"
        )

        async with client_serving(fixture("ollama_tags.json"), "/api/tags") as client:
            models = await OllamaCatalog().fetch(provider, client)

        assert [model.id for model in models] == ["qwen3:4b", "llama3.2:3b"]


class TestAdapterSelection:
    def test_a_provider_without_a_catalog_is_refused_with_the_options(self) -> None:
        provider = Provider(name="p", base_url="http://x/v1")

        with pytest.raises(CatalogError) as exc_info:
            adapter_for(provider)

        assert "declares no catalog" in str(exc_info.value)
        assert "ollama" in str(exc_info.value)

    def test_each_declared_catalog_maps_to_its_adapter(self) -> None:
        for name, expected in (
            ("openrouter", "openrouter"),
            ("openai_compat", "openai_compat"),
            ("ollama", "ollama"),
        ):
            provider = Provider(name="p", base_url="http://x/v1", catalog=name)
            assert adapter_for(provider).kind == expected


class TestQueryFilters:
    CATALOG: ClassVar[list[CatalogModel]] = [
        CatalogModel(
            id="free-big",
            context_length=65536,
            prompt_price_usd=0,
            completion_price_usd=0,
            throughput=50,
        ),
        CatalogModel(
            id="free-small",
            context_length=8192,
            prompt_price_usd=0,
            completion_price_usd=0,
            throughput=99,
        ),
        CatalogModel(
            id="paid-big",
            context_length=128000,
            prompt_price_usd=0.001,
            completion_price_usd=0.002,
            throughput=140,
        ),
    ]
    FULL = OpenRouterCatalog().capabilities
    NAMES_ONLY = OllamaCatalog().capabilities

    def apply(self, **query: Any) -> list[str]:
        selected = apply_query(
            DiscoverQuery(provider="p", **query),
            self.CATALOG,
            self.FULL,
            "p",
            "openrouter",
        )
        return [model.id for model in selected]

    def test_free_keeps_only_zero_priced_models(self) -> None:
        assert self.apply(free=True) == ["free-big", "free-small"]

    def test_min_context_filters_by_window(self) -> None:
        assert self.apply(min_context=32000) == ["free-big", "paid-big"]

    def test_include_and_exclude_match_on_the_id(self) -> None:
        assert self.apply(include=("free",)) == ["free-big", "free-small"]
        assert self.apply(exclude=("paid",)) == ["free-big", "free-small"]

    def test_sort_by_throughput_is_descending(self) -> None:
        assert self.apply(sort="throughput")[0] == "paid-big"

    def test_sort_by_price_is_cheapest_first(self) -> None:
        assert self.apply(sort="price")[-1] == "paid-big"

    def test_take_limits_after_filtering(self) -> None:
        assert self.apply(free=True, sort="throughput", take=1) == ["free-small"]

    def test_requested_filters_lists_only_what_was_declared(self) -> None:
        query = DiscoverQuery(provider="p", free=True, take=2)

        assert set(requested_filters(query)) == {"free", "take"}


class TestCapabilityRefusals:
    def test_a_name_only_catalog_refuses_a_pricing_filter(self) -> None:
        with pytest.raises(UnsupportedFilterError) as exc_info:
            apply_query(
                DiscoverQuery(provider="local", free=True),
                [CatalogModel(id="qwen3:4b")],
                OllamaCatalog().capabilities,
                "local",
                "ollama",
            )

        message = str(exc_info.value)
        assert "local" in message and "ollama" in message
        assert "free" in message and "pricing" in message

    def test_the_refusal_names_every_unanswerable_filter(self) -> None:
        with pytest.raises(UnsupportedFilterError) as exc_info:
            apply_query(
                DiscoverQuery(provider="local", free=True, min_context=32000),
                [CatalogModel(id="qwen3:4b")],
                OllamaCatalog().capabilities,
                "local",
                "ollama",
            )

        assert set(exc_info.value.filters) == {"free", "min_context"}

    def test_a_name_only_catalog_still_answers_name_filters(self) -> None:
        selected = apply_query(
            DiscoverQuery(provider="local", include=("qwen",), take=1),
            [CatalogModel(id="qwen3:4b"), CatalogModel(id="llama3.2:3b")],
            OllamaCatalog().capabilities,
            "local",
            "ollama",
        )

        assert [model.id for model in selected] == ["qwen3:4b"]

    def test_sorting_by_data_the_catalog_lacks_is_refused(self) -> None:
        """A rich catalog that reports no throughput cannot sort by it."""
        with pytest.raises(UnsupportedFilterError, match="sort"):
            apply_query(
                DiscoverQuery(provider="p", sort="throughput"),
                [CatalogModel(id="a", prompt_price_usd=0)],
                OpenRouterCatalog().capabilities,
                "p",
                "openrouter",
            )


class TestPoolResolution:
    def registry(self) -> Registry:
        return Registry(
            providers=[
                openrouter_provider(),
                Provider(name="paid", base_url="http://paid/v1"),
            ],
            models=[
                ModelConfig(name="pinned", provider="paid", model_id="paid/pinned"),
                ModelConfig(name="mop", provider="paid", model_id="paid/mop"),
            ],
        )

    async def test_declared_and_discovered_members_are_ordered(self) -> None:
        pool = Pool(
            name="mixed",
            entries=(
                PoolEntry(model="pinned"),
                PoolEntry(
                    discover=DiscoverQuery(
                        provider="openrouter", free=True, sort="throughput", take=2
                    )
                ),
            ),
            mopup="mop",
        )

        async with client_serving(fixture("openrouter_models.json")) as client:
            resolved = await resolve_pool(pool, self.registry(), client)

        assert resolved.member_names == (
            "pinned",
            "mistralai/mistral-small-3.1:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        )
        assert [member.source for member in resolved.members] == [
            "declared",
            "discovered",
            "discovered",
        ]
        assert resolved.mopup is not None
        assert resolved.mopup.name == "mop"

    async def test_discovered_members_carry_their_pricing(self) -> None:
        pool = Pool(
            name="paid-discovery",
            entries=(
                PoolEntry(
                    discover=DiscoverQuery(
                        provider="openrouter", include=("gpt-4o-mini",)
                    )
                ),
            ),
        )

        async with client_serving(fixture("openrouter_models.json")) as client:
            resolved = await resolve_pool(pool, self.registry(), client)

        assert resolved.members[0].prompt_price_usd == 0.00000015

    async def test_a_query_matching_nothing_is_refused(self) -> None:
        pool = Pool(
            name="empty",
            entries=(
                PoolEntry(
                    discover=DiscoverQuery(provider="openrouter", include=("nope",))
                ),
            ),
        )

        async with client_serving(fixture("openrouter_models.json")) as client:
            with pytest.raises(CatalogError, match="matched no models"):
                await resolve_pool(pool, self.registry(), client)

    async def test_a_model_appearing_twice_is_kept_once(self) -> None:
        pool = Pool(
            name="overlapping",
            entries=(
                PoolEntry(
                    discover=DiscoverQuery(provider="openrouter", include=("qwen",))
                ),
                PoolEntry(
                    discover=DiscoverQuery(provider="openrouter", include=("qwen",))
                ),
            ),
        )

        async with client_serving(fixture("openrouter_models.json")) as client:
            resolved = await resolve_pool(pool, self.registry(), client)

        assert len(resolved.members) == 1

    def test_members_convert_to_runnable_models(self) -> None:
        resolved = ResolvedPool(
            pool="p",
            members=[
                ResolvedMember(
                    name="a",
                    provider="paid",
                    model_id="paid/a",
                    prompt_price_usd=0.5,
                    completion_price_usd=1.0,
                )
            ],
            mopup=ResolvedMember(name="m", provider="paid", model_id="paid/m"),
        )

        configs = to_model_configs(resolved)

        assert [config.name for config in configs] == ["a", "m"]
        assert configs[0].cost_of(2, 3) == 4.0

    def test_a_model_without_pricing_reports_no_cost(self) -> None:
        member = ResolvedMember(name="a", provider="p", model_id="p/a")

        assert member_to_model(member).cost_of(100, 100) is None
