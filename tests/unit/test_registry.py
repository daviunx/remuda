"""FR-3 — named providers, models and pools, layered and reference-checked."""

from pathlib import Path
from typing import Any

import pytest

from remuda.registry import (
    DiscoverQuery,
    ModelConfig,
    Pool,
    PoolEntry,
    Provider,
    Registry,
    RegistryError,
    RegistryValidationError,
    load_layer,
    load_registry,
)
from remuda.spec import Job

OPENROUTER = {
    "openrouter": {
        "kind": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
    }
}
MODELS = {
    "free-a": {"provider": "openrouter", "model_id": "vendor/a:free"},
    "free-b": {"provider": "openrouter", "model_id": "vendor/b:free"},
}


def registry_of(**overrides: Any) -> Registry:
    return Registry(
        providers=[Provider(name="local", base_url="http://localhost:11434/v1")],
        models=[ModelConfig(name="small", provider="local", model_id="qwen3:4b")],
        pools=[Pool(name="cheap", entries=(PoolEntry(model="small"),))],
        **overrides,
    )


class TestProgrammaticConstruction:
    def test_registry_is_buildable_without_any_files(self) -> None:
        registry = registry_of()

        assert registry.validate() == []
        assert registry.pool("cheap").model_names == ("small",)
        assert registry.model("small").concurrency == 4

    def test_unknown_name_lookup_lists_what_is_registered(self) -> None:
        registry = registry_of()

        with pytest.raises(RegistryError) as exc_info:
            registry.pool("nope")

        message = str(exc_info.value)
        assert "nope" in message
        assert "cheap" in message

    def test_dangling_model_reference_is_reported(self) -> None:
        registry = Registry(
            providers=[Provider(name="local", base_url="http://localhost:11434/v1")],
            models=[],
            pools=[Pool(name="cheap", entries=(PoolEntry(model="ghost"),))],
        )

        defects = registry.validate()

        assert any("cheap" in d and "ghost" in d for d in defects), defects

    def test_dangling_provider_reference_is_reported(self) -> None:
        registry = Registry(
            models=[ModelConfig(name="small", provider="ghost", model_id="x")],
        )

        defects = registry.validate()

        assert any("small" in d and "ghost" in d for d in defects), defects

    def test_validate_or_raise_names_every_defect(self) -> None:
        registry = Registry(
            models=[ModelConfig(name="small", provider="ghost", model_id="x")],
        )

        with pytest.raises(RegistryValidationError) as exc_info:
            registry.validate_or_raise()

        assert exc_info.value.defects

    def test_duplicate_name_in_one_layer_is_refused(self) -> None:
        with pytest.raises(RegistryValidationError) as exc_info:
            Registry(
                providers=[
                    Provider(name="local", base_url="http://a/v1"),
                    Provider(name="local", base_url="http://b/v1"),
                ]
            )

        assert "local" in str(exc_info.value)


class TestCredentialHygiene:
    def test_inline_authorization_header_is_refused(self) -> None:
        with pytest.raises(ValueError, match="key_env") as exc_info:
            Provider(
                name="openrouter",
                base_url="https://openrouter.ai/api/v1",
                headers={"Authorization": "Bearer sk-live-123"},
            )

        assert "openrouter" in str(exc_info.value)

    def test_key_env_names_a_variable_not_a_value(self) -> None:
        provider = Provider(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            key_env="OPENROUTER_API_KEY",
        )

        assert provider.key_env == "OPENROUTER_API_KEY"

    def test_openai_compat_provider_needs_a_base_url(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            Provider(name="broken")


class TestDiscoverQueries:
    def test_query_shape_is_stored_for_later_resolution(self) -> None:
        pool = Pool(
            name="discovered",
            entries=(
                PoolEntry(
                    discover=DiscoverQuery(
                        provider="openrouter",
                        free=True,
                        min_context=32000,
                        sort="throughput",
                        take=4,
                    )
                ),
            ),
        )

        assert pool.model_names == ()
        assert pool.discover_queries[0].take == 4

    def test_query_without_any_filter_is_refused(self) -> None:
        with pytest.raises(ValueError, match="filter"):
            DiscoverQuery(provider="openrouter")

    def test_entry_with_both_model_and_discover_is_refused(self) -> None:
        with pytest.raises(ValueError, match="either"):
            PoolEntry(model="a", discover=DiscoverQuery(provider="p", free=True))


class TestLayering:
    def test_project_layer_overrides_the_user_layer_by_name(
        self, write_registry_dir: Any
    ) -> None:
        user = write_registry_dir(
            providers=OPENROUTER,
            models=MODELS,
            pools={"free-fast": ["free-a"]},
            name="user",
        )
        project = write_registry_dir(
            pools={"free-fast": {"strategy": "waterfall", "models": ["free-b"]}},
            name="project",
        )

        registry = load_registry([user, project])

        pool = registry.pool("free-fast")
        assert pool.strategy == "waterfall"
        assert pool.model_names == ("free-b",)
        assert set(registry.models) == {"free-a", "free-b"}

    def test_missing_layer_directory_is_an_empty_layer(self, tmp_path: Path) -> None:
        layer = load_layer(tmp_path / "absent")

        assert layer.pools == {}

    def test_pool_shorthand_list_becomes_entries(self, write_registry_dir: Any) -> None:
        directory = write_registry_dir(
            providers=OPENROUTER,
            models=MODELS,
            pools={"free-fast": ["free-a", "free-b"]},
        )

        registry = load_registry([directory])

        assert registry.pool("free-fast").model_names == ("free-a", "free-b")

    def test_pool_may_mix_named_models_and_a_discover_query(
        self, write_registry_dir: Any
    ) -> None:
        directory = write_registry_dir(
            providers=OPENROUTER,
            models=MODELS,
            pools={
                "mixed": {
                    "models": ["free-a"],
                    "entries": [{"discover": {"provider": "openrouter", "free": True}}],
                    "mopup": "free-b",
                }
            },
        )

        pool = load_registry([directory]).pool("mixed")

        assert pool.model_names == ("free-a",)
        assert pool.discover_queries[0].provider == "openrouter"
        assert pool.mopup == "free-b"

    def test_merged_registry_is_validated(self, write_registry_dir: Any) -> None:
        directory = write_registry_dir(
            providers=OPENROUTER, pools={"free-fast": ["free-a"]}
        )

        with pytest.raises(RegistryValidationError) as exc_info:
            load_registry([directory])

        assert any("free-a" in defect for defect in exc_info.value.defects)

    def test_malformed_declaration_names_the_entry(
        self, write_registry_dir: Any
    ) -> None:
        directory = write_registry_dir(
            providers={"openrouter": {"kind": "carrier-pigeon"}}
        )

        with pytest.raises(RegistryValidationError) as exc_info:
            load_registry([directory])

        assert any("openrouter" in defect for defect in exc_info.value.defects)


class TestJobCrossCheck:
    def test_unknown_pool_names_the_field_and_the_pool(
        self, write_job_dir: Any
    ) -> None:
        job = self._job(write_job_dir, pool="does-not-exist")

        defects = registry_of().check_job(job)

        assert any(
            "severity" in d and "does-not-exist" in d and "cheap" in d for d in defects
        ), defects

    def test_known_pool_passes(self, write_job_dir: Any) -> None:
        job = self._job(write_job_dir, pool="cheap")

        assert registry_of().check_job(job) == []

    def test_pool_whose_model_is_missing_is_reported_against_the_field(
        self, write_job_dir: Any
    ) -> None:
        job = self._job(write_job_dir, pool="broken")
        registry = Registry(
            providers=[Provider(name="local", base_url="http://localhost/v1")],
            pools=[Pool(name="broken", entries=(PoolEntry(model="ghost"),))],
        )

        defects = registry.check_job(job)

        assert any("severity" in d and "ghost" in d for d in defects), defects

    @staticmethod
    def _job(write_job_dir: Any, pool: str) -> Job:
        job_dir = write_job_dir(
            {
                "name": "cross-check",
                "input": {"path": "posts.csv", "key": "post_id"},
                "fields": [
                    {
                        "name": "severity",
                        "kind": "classify",
                        "pool": pool,
                        "vocabulary": ["high", "low"],
                        "prompt": {"inputs": ["title"], "template": "{{ title }}"},
                    }
                ],
            },
            {"posts.csv": "post_id,title\n1,Leak\n"},
        )
        return Job.from_dir(job_dir)
