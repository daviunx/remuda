"""FR-2 — field kinds, preconditions and single-level dependencies.

Each test asserts the DEFECT the operator is told about, not merely that a
refusal happened: a refusal that does not name what is wrong is not usable.
"""

from typing import Any

import pytest

from remuda.spec import Job, SpecValidationError

VOCABULARY = "critical\nhigh\nlow\n"
INPUT_CSV = "post_id,title,body,brief\n1,Leak,Water,A brief\n"

BASE_FILES = {"vocab/severity.txt": VOCABULARY, "posts.csv": INPUT_CSV}


def job_spec(*fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "rules",
        "input": {"path": "posts.csv", "key": "post_id"},
        "fields": list(fields),
    }


def classify_field(**overrides: Any) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": "severity",
        "kind": "classify",
        "pool": "free-fast",
        "vocabulary_file": "vocab/severity.txt",
        "prompt": {"inputs": ["title"], "template": "{{ title }}?"},
    }
    field.update(overrides)
    return field


def generate_field(name: str, **overrides: Any) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "kind": "generate",
        "pool": "free-fast",
        "prompt": {"inputs": ["body"], "template": "Summarize {{ body }}"},
    }
    field.update(overrides)
    return field


def refusal(write_job_dir: Any, spec: dict[str, Any]) -> tuple[str, ...]:
    """Load a job expected to be refused and return its defect lines."""
    job_dir = write_job_dir(spec, BASE_FILES)
    with pytest.raises(SpecValidationError) as exc_info:
        Job.from_dir(job_dir)
    return exc_info.value.defects


class TestKindDeclarations:
    def test_classify_without_a_vocabulary_is_refused(self, write_job_dir: Any) -> None:
        field = classify_field()
        del field["vocabulary_file"]

        defects = refusal(write_job_dir, job_spec(field))

        assert any("severity" in d and "vocabulary" in d for d in defects), defects

    def test_score_without_a_range_is_refused(self, write_job_dir: Any) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(
                {
                    "name": "quality",
                    "kind": "score",
                    "pool": "free-fast",
                    "prompt": {"inputs": ["body"], "template": "{{ body }}"},
                }
            ),
        )

        assert any("quality" in d and "range" in d for d in defects), defects

    def test_map_field_with_a_pool_is_refused(self, write_job_dir: Any) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(
                {
                    "name": "industry",
                    "kind": "map",
                    "pool": "free-fast",
                    "map": {"lookup": "title", "table": {"Leak": "plumbing"}},
                }
            ),
        )

        assert any("industry" in d and "pool" in d and "map" in d for d in defects), (
            defects
        )

    def test_map_field_needs_no_model(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(
            job_spec(
                {
                    "name": "industry",
                    "kind": "map",
                    "map": {"lookup": "title", "table": {"Leak": "plumbing"}},
                }
            ),
            BASE_FILES,
        )

        job = Job.from_dir(job_dir)

        industry = job.field("industry")
        assert industry.pool is None
        assert industry.prompt is None
        assert industry.map_table is not None
        assert industry.map_table.table == {"Leak": "plumbing"}

    def test_vocabulary_on_a_generate_field_is_refused(
        self, write_job_dir: Any
    ) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(generate_field("hook", vocabulary=["a", "b"])),
        )

        assert any(
            "hook" in d and "vocabulary" in d and "generate" in d for d in defects
        ), defects

    def test_duplicate_vocabulary_labels_are_refused(self, write_job_dir: Any) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(
                classify_field(
                    vocabulary_file=None,
                    vocabulary=["high", "high", "low"],
                )
            ),
        )

        assert any("duplicate" in d and "high" in d for d in defects), defects

    def test_unknown_kind_is_reported_before_anything_else(
        self, write_job_dir: Any
    ) -> None:
        defects = refusal(write_job_dir, job_spec(classify_field(kind="summarise")))

        assert any(
            "severity" in d and "summarise" in d and "classify" in d for d in defects
        ), defects

    def test_missing_kind_names_the_accepted_kinds(self, write_job_dir: Any) -> None:
        field = classify_field()
        del field["kind"]

        defects = refusal(write_job_dir, job_spec(field))

        assert any("kind" in d and "score" in d for d in defects), defects


class TestExtractSchema:
    def test_enum_property_without_values_is_refused(self, write_job_dir: Any) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(
                {
                    "name": "facts",
                    "kind": "extract",
                    "pool": "free-fast",
                    "prompt": {"inputs": ["body"], "template": "{{ body }}"},
                    "schema": {"properties": {"tier": {"type": "enum"}}},
                }
            ),
        )

        assert any("enum" in d and "values" in d for d in defects), defects

    def test_declared_schema_survives_loading(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(
            job_spec(
                {
                    "name": "facts",
                    "kind": "extract",
                    "pool": "free-fast",
                    "prompt": {"inputs": ["body"], "template": "{{ body }}"},
                    "schema": {
                        "properties": {
                            "tier": {"type": "enum", "values": ["a", "b"]},
                            "confirmed": {"type": "boolean", "required": False},
                        }
                    },
                }
            ),
            BASE_FILES,
        )

        schema = Job.from_dir(job_dir).field("facts").extract_schema

        assert schema is not None
        assert schema.properties["tier"].values == ("a", "b")
        assert schema.properties["confirmed"].required is False
        assert schema.is_nested is True


class TestDependenciesAndPreconditions:
    def test_dependency_chain_is_refused_naming_both_links(
        self, write_job_dir: Any
    ) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(
                generate_field("brief"),
                generate_field("hook", depends_on="brief"),
                generate_field("cta", depends_on="hook"),
            ),
        )

        assert any(
            "cta" in d and "hook" in d and "brief" in d and "chain" in d.lower()
            for d in defects
        ), defects

    def test_single_level_dependency_is_accepted(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(
            job_spec(
                generate_field("brief"),
                generate_field("hook", depends_on="brief"),
            ),
            BASE_FILES,
        )

        job = Job.from_dir(job_dir)

        assert job.field("hook").depends_on == "brief"

    def test_dependency_on_an_undeclared_field_is_refused(
        self, write_job_dir: Any
    ) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(generate_field("hook", depends_on="nowhere")),
        )

        assert any("hook" in d and "nowhere" in d for d in defects), defects

    def test_precondition_on_another_field_requires_depends_on(
        self, write_job_dir: Any
    ) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(
                generate_field("brief"),
                generate_field("hook", when={"source": "brief", "min_length": 40}),
            ),
        )

        assert any(
            "hook" in d and "brief" in d and "depends_on" in d for d in defects
        ), defects

    def test_precondition_on_a_dependency_is_accepted(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(
            job_spec(
                generate_field("brief"),
                generate_field(
                    "hook",
                    depends_on="brief",
                    when={"source": "brief", "min_length": 40},
                ),
            ),
            BASE_FILES,
        )

        precondition = Job.from_dir(job_dir).field("hook").when

        assert precondition is not None
        assert precondition.source == "brief"
        assert precondition.min_length == 40

    def test_duplicate_field_names_are_refused(self, write_job_dir: Any) -> None:
        defects = refusal(
            write_job_dir,
            job_spec(generate_field("hook"), generate_field("hook")),
        )

        assert any("duplicate" in d and "hook" in d for d in defects), defects


class TestJobFile:
    def test_missing_job_file_is_refused(self, tmp_path: Any) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()

        with pytest.raises(SpecValidationError) as exc_info:
            Job.from_dir(empty)

        assert "job.yaml" in str(exc_info.value)

    def test_unparseable_yaml_is_refused(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(job_spec(classify_field()), BASE_FILES)
        (job_dir / "job.yaml").write_text("name: [unclosed\n", encoding="utf-8")

        with pytest.raises(SpecValidationError) as exc_info:
            Job.from_dir(job_dir)

        assert "YAML" in str(exc_info.value)

    def test_unknown_top_level_key_is_refused(self, write_job_dir: Any) -> None:
        spec = job_spec(classify_field())
        spec["chunk_size"] = 20

        defects = refusal(write_job_dir, spec)

        assert any("chunk_size" in d for d in defects), defects
