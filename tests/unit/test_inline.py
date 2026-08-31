"""FR-11/FR-13 — one-shot and inline bulk, synthesized onto the normal engine.

Neither mode may become a second execution path: both build an ordinary
one-field job and hand it to the same runner, ladder and validators.
"""

from pathlib import Path
from typing import Any

import pytest

from remuda.inline import (
    InlineSpecError,
    default_key,
    input_columns,
    one_shot_row,
    synthesize_job,
)
from remuda.spec.errors import SpecValidationError


def job_of(prompt: str, **overrides: Any) -> Any:
    options: dict[str, Any] = {
        "prompt": prompt,
        "field": "answer",
        "pool": "cheap",
        "columns": ("id", "title", "body"),
        "key": "id",
    }
    return synthesize_job(**{**options, **overrides})


class TestSynthesis:
    def test_a_prompt_becomes_a_one_field_generate_job(self) -> None:
        job = job_of("Summarize {{ title }}")

        assert job.field_names == ("answer",)
        field = job.field("answer")
        assert field.kind == "generate"
        assert field.pool == "cheap"
        assert field.prompt is not None
        assert field.prompt.template == "Summarize {{ title }}"

    def test_a_vocabulary_makes_it_a_classify_job(self) -> None:
        job = job_of("Severity of {{ title }}?", vocabulary=("high", "low"))

        field = job.field("answer")
        assert field.kind == "classify"
        assert field.vocabulary == ("high", "low")

    def test_only_the_columns_the_template_reads_are_declared(self) -> None:
        """The allowlist firewall still holds: inputs are derived, not waived."""
        job = job_of("Title: {{ title }}")

        prompt = job.field("answer").prompt
        assert prompt is not None
        assert prompt.inputs == ("title",)

    def test_a_template_variable_that_is_not_a_column_is_refused(self) -> None:
        with pytest.raises(InlineSpecError) as exc_info:
            job_of("Title: {{ title }} Author: {{ author }}")

        message = str(exc_info.value)
        assert "author" in message
        assert "title" in message  # the columns that DO exist are named

    def test_the_field_name_may_be_chosen(self) -> None:
        job = job_of("{{ title }}", field="severity")

        assert job.field_names == ("severity",)

    def test_the_key_column_is_carried_into_the_spec(self) -> None:
        job = job_of("{{ title }}", key="post_id", columns=("post_id", "title"))

        assert job.input.key == "post_id"

    def test_a_key_column_absent_from_the_input_is_refused(self) -> None:
        with pytest.raises(InlineSpecError, match="ghost"):
            job_of("{{ title }}", key="ghost")

    def test_execution_settings_ride_through(self) -> None:
        job = job_of("{{ title }}", records_per_call=25, workers=5)

        assert job.execution.records_per_call == 25
        assert job.execution.workers == 5

    def test_a_prompt_reading_no_column_is_still_valid(self) -> None:
        """A constant prompt per row is unusual but not wrong."""
        job = job_of("Say OK")

        prompt = job.field("answer").prompt
        assert prompt is not None
        assert prompt.inputs == ()

    def test_an_empty_prompt_is_refused(self) -> None:
        with pytest.raises((InlineSpecError, SpecValidationError, ValueError)):
            job_of("   ")

    def test_the_synthesized_job_is_a_normal_job(self) -> None:
        """No special-casing downstream: it must pass the same validation."""
        job = job_of("{{ title }}", vocabulary=("a", "b"))

        assert job.name
        assert job.input.path
        assert job.field("answer").kind == "classify"


class TestOneShotSynthesis:
    def test_a_one_shot_declares_a_single_synthetic_row(self) -> None:
        row = one_shot_row(None)

        assert row == {"id": "1", "input": ""}

    def test_piped_text_becomes_the_input_column(self) -> None:
        row = one_shot_row("some piped text")

        assert row["input"] == "some piped text"

    def test_a_one_shot_prompt_may_read_the_piped_input(self) -> None:
        job = job_of("Classify: {{ input }}", columns=("id", "input"), key="id")

        prompt = job.field("answer").prompt
        assert prompt is not None
        assert prompt.inputs == ("input",)


class TestInputColumns:
    def test_columns_are_read_from_a_csv_header(self, tmp_path: Path) -> None:
        path = tmp_path / "in.csv"
        path.write_text("post_id,title,body\n1,a,b\n", encoding="utf-8")

        assert input_columns(path) == ("post_id", "title", "body")

    def test_the_first_column_is_the_default_key(self, tmp_path: Path) -> None:
        assert default_key(("post_id", "title")) == "post_id"

    def test_a_file_with_no_columns_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")

        with pytest.raises(Exception):  # noqa: B017 - any refusal, never silence
            input_columns(path)
