"""The prompt-input allowlist firewall (FR-1, FR-2).

A template is rendered from a context built only from `prompt.inputs`, so an
undeclared column is unreachable rather than merely unused.
"""

from typing import Any

import pytest

from remuda.spec import (
    FieldSpec,
    Job,
    PromptRenderError,
    PromptSpec,
    SpecValidationError,
    render_prompt,
    template_variables,
)

ROW = {
    "post_id": "1",
    "title": "Leak in unit 4",
    "body": "Water everywhere",
    "internal_notes": "TENANT-SECRET-42",
}


def field_with(inputs: list[str], template: str) -> FieldSpec:
    return FieldSpec(
        name="severity",
        kind="generate",
        pool="free-fast",
        prompt=PromptSpec(inputs=tuple(inputs), template=template),
    )


class TestRenderingSeesOnlyDeclaredInputs:
    def test_undeclared_column_is_invisible_to_the_template(self) -> None:
        field = field_with(["title"], "Title: {{ title }}")

        rendered = field.render(ROW)

        assert "Leak in unit 4" in rendered
        assert "TENANT-SECRET-42" not in rendered

    def test_positive_control_declared_column_does_reach_the_template(self) -> None:
        """Proves the assertion above is about the allowlist, not the fixture."""
        field = field_with(
            ["title", "internal_notes"],
            "Title: {{ title }} Notes: {{ internal_notes }}",
        )

        rendered = field.render(ROW)

        assert "TENANT-SECRET-42" in rendered

    def test_row_missing_a_declared_input_is_refused(self) -> None:
        field = field_with(["title", "author"], "{{ title }} {{ author }}")

        with pytest.raises(PromptRenderError) as exc_info:
            field.render({"title": "Leak"})

        assert "author" in str(exc_info.value)

    def test_map_field_has_no_prompt_to_render(self) -> None:
        field = FieldSpec.model_validate(
            {
                "name": "industry",
                "kind": "map",
                "map": {"lookup": "title", "table": {"Leak": "plumbing"}},
            }
        )

        with pytest.raises(PromptRenderError) as exc_info:
            field.render(ROW)

        assert "industry" in str(exc_info.value)


class TestTemplateVariables:
    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("plain text", set()),
            ("{{ a }}", {"a"}),
            ("{% if a %}{{ b }}{% endif %}", {"a", "b"}),
            ("{{ a }}{{ a }}", {"a"}),
        ],
        ids=["none", "one", "conditional", "repeated"],
    )
    def test_variables_are_discovered(self, template: str, expected: set[str]) -> None:
        assert template_variables(template) == frozenset(expected)

    def test_invalid_jinja_is_refused(self) -> None:
        with pytest.raises(PromptRenderError):
            template_variables("{{ unclosed ")


class TestRenderPromptDirectly:
    def test_context_is_built_from_the_allowlist_only(self) -> None:
        rendered = render_prompt("{{ title }}", ["title"], ROW)

        assert rendered == "Leak in unit 4"


class TestFirewallIsEnforcedAtLoad:
    def test_system_prompt_variable_must_also_be_declared(
        self, write_job_dir: Any
    ) -> None:
        job_dir = write_job_dir(
            {
                "name": "firewall",
                "input": {"path": "posts.csv", "key": "post_id"},
                "fields": [
                    {
                        "name": "severity",
                        "kind": "generate",
                        "pool": "free-fast",
                        "prompt": {
                            "inputs": ["title"],
                            "system": "You rate {{ internal_notes }}.",
                            "template": "{{ title }}",
                        },
                    }
                ],
            },
            {"posts.csv": "post_id,title\n1,Leak\n"},
        )

        with pytest.raises(SpecValidationError) as exc_info:
            Job.from_dir(job_dir)

        message = str(exc_info.value)
        assert "internal_notes" in message
        assert "inputs" in message
