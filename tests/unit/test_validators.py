"""FR-2/FR-4 — per-kind validation and the repair feedback a retry carries."""

from typing import Any

import pytest

from remuda.spec.models import FieldSpec
from remuda.spec.shapes import Precondition
from remuda.validate import evaluate_map, precondition_met, validate_answer


def field(**declaration: Any) -> FieldSpec:
    base: dict[str, Any] = {
        "name": "f",
        "pool": "p",
        "prompt": {"inputs": ["text"], "template": "{{ text }}"},
    }
    return FieldSpec.model_validate({**base, **declaration})


CLASSIFY = field(kind="classify", vocabulary=["critical", "high", "low"])


class TestClassify:
    @pytest.mark.parametrize(
        "raw",
        ["high", "  high  ", "High", '"high"', "high.", "`HIGH`"],
        ids=["plain", "padded", "cased", "quoted", "dotted", "backticked"],
    )
    def test_normalizes_to_the_declared_label(self, raw: str) -> None:
        verdict = validate_answer(CLASSIFY, raw)

        assert verdict.is_valid
        assert verdict.value == "high"

    def test_out_of_vocabulary_repair_lists_the_allowed_labels(self) -> None:
        verdict = validate_answer(CLASSIFY, "urgent")

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "urgent" in verdict.repair
        assert "critical, high, low" in verdict.repair

    def test_empty_answer_is_refused(self) -> None:
        verdict = validate_answer(CLASSIFY, "   ")

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "critical" in verdict.repair

    def test_prose_around_the_label_is_refused_not_guessed(self) -> None:
        """Fishing a label out of a sentence would invent a decision."""
        verdict = validate_answer(CLASSIFY, "I would say this is high, probably")

        assert not verdict.is_valid


class TestScore:
    SCORE = field(kind="score", range={"minimum": 1, "maximum": 5, "integer": True})

    def test_number_inside_the_range_is_accepted(self) -> None:
        assert validate_answer(self.SCORE, "4").value == 4

    def test_number_is_found_in_a_short_sentence(self) -> None:
        assert validate_answer(self.SCORE, "Score: 3").value == 3

    def test_out_of_range_repair_names_the_bounds(self) -> None:
        verdict = validate_answer(self.SCORE, "9")

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "1.0" in verdict.repair and "5.0" in verdict.repair

    def test_fractional_answer_is_refused_when_integer_declared(self) -> None:
        verdict = validate_answer(self.SCORE, "3.5")

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "whole number" in verdict.repair

    def test_fractional_answer_is_accepted_when_integer_not_declared(self) -> None:
        continuous = field(kind="score", range={"minimum": 0, "maximum": 1})

        assert validate_answer(continuous, "0.25").value == 0.25

    def test_no_number_at_all_is_refused(self) -> None:
        assert not validate_answer(self.SCORE, "quite good").is_valid


class TestGenerate:
    def test_free_text_without_constraints_is_accepted(self) -> None:
        assert validate_answer(field(kind="generate"), " hello ").value == "hello"

    def test_too_short_repair_names_both_lengths(self) -> None:
        constrained = field(kind="generate", constraints={"min_chars": 20})

        verdict = validate_answer(constrained, "short")

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "5 characters" in verdict.repair and "20" in verdict.repair

    def test_too_long_is_refused(self) -> None:
        constrained = field(kind="generate", constraints={"max_chars": 5})

        assert not validate_answer(constrained, "far too long").is_valid

    def test_single_line_collapses_whitespace(self) -> None:
        constrained = field(kind="generate", constraints={"single_line": True})

        assert validate_answer(constrained, "a\n b\tc").value == "a b c"


class TestExtract:
    EXTRACT = field(
        kind="extract",
        schema={
            "properties": {
                "tier": {"type": "enum", "values": ["gold", "silver"]},
                "seats": {"type": "integer"},
                "confirmed": {"type": "boolean", "required": False},
            }
        },
    )

    def test_json_object_is_parsed_and_coerced(self) -> None:
        verdict = validate_answer(
            self.EXTRACT, '{"tier": "GOLD", "seats": "12", "confirmed": "yes"}'
        )

        assert verdict.value == {"tier": "gold", "seats": 12, "confirmed": True}

    def test_fenced_json_is_accepted(self) -> None:
        raw = 'Here you go:\n```json\n{"tier": "silver", "seats": 3}\n```'

        assert validate_answer(self.EXTRACT, raw).value["tier"] == "silver"

    def test_optional_key_may_be_absent(self) -> None:
        verdict = validate_answer(self.EXTRACT, '{"tier": "gold", "seats": 1}')

        assert "confirmed" not in verdict.value

    def test_missing_required_key_repair_names_it(self) -> None:
        verdict = validate_answer(self.EXTRACT, '{"tier": "gold"}')

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "seats" in verdict.repair

    def test_value_outside_the_enum_repair_names_the_key(self) -> None:
        verdict = validate_answer(self.EXTRACT, '{"tier": "bronze", "seats": 2}')

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "tier" in verdict.repair and "gold" in verdict.repair

    def test_non_json_answer_is_refused(self) -> None:
        verdict = validate_answer(self.EXTRACT, "tier is gold, 2 seats")

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "JSON object" in verdict.repair


class TestMap:
    MAP = field(
        kind="map",
        pool=None,
        prompt=None,
        map={"lookup": "country", "table": {"ES": "Spain", "FR": "France"}},
    )

    def test_lookup_is_case_insensitive_by_default(self) -> None:
        assert evaluate_map(self.MAP, {"country": "es"}).value == "Spain"

    def test_unmapped_value_is_refused_naming_the_column(self) -> None:
        verdict = evaluate_map(self.MAP, {"country": "PT"})

        assert not verdict.is_valid
        assert verdict.repair is not None
        assert "PT" in verdict.repair and "country" in verdict.repair

    def test_default_answers_unmapped_values_when_declared(self) -> None:
        with_default = field(
            kind="map",
            pool=None,
            prompt=None,
            map={"lookup": "country", "table": {"ES": "Spain"}, "default": "other"},
        )

        assert evaluate_map(with_default, {"country": "PT"}).value == "other"

    def test_case_sensitive_table_does_not_fold(self) -> None:
        strict = field(
            kind="map",
            pool=None,
            prompt=None,
            map={
                "lookup": "country",
                "table": {"ES": "Spain"},
                "case_sensitive": True,
            },
        )

        assert not evaluate_map(strict, {"country": "es"}).is_valid

    def test_model_answer_validation_refuses_a_map_field(self) -> None:
        with pytest.raises(ValueError, match="no model answer"):
            validate_answer(self.MAP, "anything")


class TestPreconditions:
    def test_empty_source_fails_the_default_guard(self) -> None:
        is_met, reason = precondition_met(Precondition(source="brief"), "  ")

        assert is_met is False
        assert "brief" in reason

    def test_min_length_reports_both_lengths(self) -> None:
        is_met, reason = precondition_met(
            Precondition(source="brief", min_length=40), "too short"
        )

        assert is_met is False
        assert "9 characters" in reason and "40" in reason

    def test_one_of_membership(self) -> None:
        guard = Precondition(source="tier", one_of=("gold", "silver"))

        assert precondition_met(guard, "gold")[0] is True
        assert precondition_met(guard, "bronze")[0] is False

    def test_equals_comparison(self) -> None:
        guard = Precondition(source="status", equals="open")

        assert precondition_met(guard, "open")[0] is True
        assert precondition_met(guard, "closed")[0] is False

    def test_none_value_fails_not_empty(self) -> None:
        assert precondition_met(Precondition(source="brief"), None)[0] is False
