"""FR-4 — packing rows into one call and unpacking the keyed answers."""

import pytest

from remuda.engine.packing import (
    chunk_keys,
    pack_prompt,
    parse_answers,
    single_prompt_with_repair,
)


class TestChunkKeys:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            (1, [("a",), ("b",), ("c",), ("d",), ("e",)]),
            (2, [("a", "b"), ("c", "d"), ("e",)]),
            (5, [("a", "b", "c", "d", "e")]),
            (9, [("a", "b", "c", "d", "e")]),
        ],
        ids=["one-each", "pairs-plus-remainder", "exact", "oversized"],
    )
    def test_grouping_preserves_input_order(
        self, size: int, expected: list[tuple[str, ...]]
    ) -> None:
        assert chunk_keys(["a", "b", "c", "d", "e"], size) == expected

    def test_no_keys_makes_no_chunks(self) -> None:
        assert chunk_keys([], 3) == []

    def test_zero_size_falls_back_to_one_row_per_call(self) -> None:
        assert chunk_keys(["a", "b"], 0) == [("a",), ("b",)]


class TestPackPrompt:
    def test_single_item_is_sent_unwrapped(self) -> None:
        """A keying instruction costs accuracy when there is one answer."""
        assert pack_prompt({"1": "Classify this"}) == "Classify this"

    def test_packed_prompt_names_every_id_and_the_reply_shape(self) -> None:
        packed = pack_prompt({"1": "First", "2": "Second"})

        assert "<id>: <answer>" in packed
        assert "1, 2" in packed
        assert "First" in packed and "Second" in packed

    def test_repair_feedback_rides_with_the_item_it_belongs_to(self) -> None:
        packed = pack_prompt(
            {"1": "First", "2": "Second"}, {"2": "'urgent' is not allowed"}
        )

        after_second = packed.split("Second", 1)[1]
        assert "'urgent' is not allowed" in after_second
        assert "'urgent' is not allowed" not in packed.split("Second", 1)[0]

    def test_single_item_repair_is_appended_to_the_prompt(self) -> None:
        asked = single_prompt_with_repair("Classify this", "not a label")

        assert asked.startswith("Classify this")
        assert "not a label" in asked

    def test_single_item_without_repair_is_unchanged(self) -> None:
        assert single_prompt_with_repair("Classify this", None) == "Classify this"


class TestParseAnswers:
    def test_single_key_takes_the_whole_body(self) -> None:
        assert parse_answers("  high  ", ["7"]) == {"7": "high"}

    def test_single_key_tolerates_an_echoed_id_prefix(self) -> None:
        """After banking, the last key is asked unwrapped — models still echo."""
        assert parse_answers("7: high", ["7"]) == {"7": "high"}

    def test_single_key_answer_keeps_a_colon_that_is_not_its_id(self) -> None:
        assert parse_answers("Summary: all good", ["7"]) == {"7": "Summary: all good"}

    def test_keyed_lines_are_split_per_key(self) -> None:
        raw = "1: high\n2: low\n"

        assert parse_answers(raw, ["1", "2"]) == {"1": "high", "2": "low"}

    @pytest.mark.parametrize(
        "raw",
        [
            "- 1: high\n- 2: low",
            "* 1 = high\n* 2 = low",
            "Here you go:\n1: high\n2: low\nHope that helps",
        ],
        ids=["bullets", "equals", "chatter-around"],
    )
    def test_framing_around_the_answers_is_tolerated(self, raw: str) -> None:
        assert parse_answers(raw, ["1", "2"]) == {"1": "high", "2": "low"}

    def test_json_object_answers_are_accepted(self) -> None:
        raw = '{"1": "high", "2": "low"}'

        assert parse_answers(raw, ["1", "2"]) == {"1": "high", "2": "low"}

    def test_partial_answers_return_only_what_was_answered(self) -> None:
        """The banking guarantee starts here: unanswered keys stay unanswered."""
        assert parse_answers("1: high\n", ["1", "2"]) == {"1": "high"}

    def test_keys_the_model_invented_are_ignored(self) -> None:
        raw = "1: high\n99: whatever"

        assert parse_answers(raw, ["1", "2"]) == {"1": "high"}

    def test_first_answer_for_a_key_wins(self) -> None:
        raw = "1: high\n1: low"

        assert parse_answers(raw, ["1", "2"])["1"] == "high"

    def test_item_headers_are_not_mistaken_for_answers(self) -> None:
        raw = "### item 1\n1: high\n### item 2\n2: low"

        assert parse_answers(raw, ["1", "2"]) == {"1": "high", "2": "low"}
