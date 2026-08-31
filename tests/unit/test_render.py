"""FR-10 — rendering a finished run: projection, enrich, fill-missing, refusals."""

import csv
import io
import json
from typing import Any, ClassVar

import pytest

from remuda.ledger.models import LedgerEntry
from remuda.ledger.render import RenderError, RenderRequest, render
from remuda.spec.models import Job

ROWS: list[dict[str, Any]] = [
    {"post_id": "1", "title": "Leak", "body": "Water", "severity": ""},
    {"post_id": "2", "title": "Noise", "body": "Loud", "severity": "low"},
]


def job_of(*fields: dict[str, Any]) -> Job:
    return Job.model_validate(
        {
            "name": "rendered",
            "input": {"path": "in.csv", "key": "post_id"},
            "fields": list(fields),
        }
    )


CLASSIFY = {
    "name": "severity",
    "kind": "classify",
    "pool": "cheap",
    "vocabulary": ["high", "low"],
    "prompt": {"inputs": ["title"], "template": "{{ title }}"},
}

JOB = job_of(CLASSIFY)

ENTRIES = [
    LedgerEntry(key="1", field="severity", outcome="ok", value="high", model="a"),
    LedgerEntry(key="2", field="severity", outcome="ok", value="high", model="a"),
]


def request(**overrides: Any) -> RenderRequest:
    return RenderRequest(job=JOB, **{"entries": ENTRIES, **overrides})


def parse_csv(rendered: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(rendered))
    return list(reader.fieldnames or []), list(reader)


class TestProjection:
    def test_csv_carries_the_key_and_the_derived_field(self) -> None:
        columns, records = parse_csv(render(request()))

        assert columns == ["post_id", "severity"]
        assert records[0] == {"post_id": "1", "severity": "high"}

    def test_jsonl_is_one_record_per_line(self) -> None:
        rendered = render(request(format="jsonl"))

        lines = [json.loads(line) for line in rendered.strip().split("\n")]
        assert lines[1]["post_id"] == "2"

    def test_json_is_an_array(self) -> None:
        payload = json.loads(render(request(format="json")))

        assert [record["severity"] for record in payload] == ["high", "high"]

    def test_a_skipped_key_renders_as_an_empty_cell(self) -> None:
        entries = [
            ENTRIES[0],
            LedgerEntry(key="2", field="severity", outcome="skipped", reason="guard"),
        ]

        _, records = parse_csv(render(request(entries=entries)))

        assert records[1]["severity"] == ""

    def test_named_passthrough_columns_are_carried(self) -> None:
        columns, records = parse_csv(render(request(passthrough=("title",), rows=ROWS)))

        assert columns == ["post_id", "title", "severity"]
        assert records[0]["title"] == "Leak"

    def test_star_carries_every_input_column(self) -> None:
        columns, _ = parse_csv(render(request(passthrough=("*",), rows=ROWS)))

        assert columns[:3] == ["post_id", "title", "body"]

    def test_unknown_passthrough_column_is_refused_by_name(self) -> None:
        with pytest.raises(RenderError, match="author"):
            render(request(passthrough=("author",), rows=ROWS))

    def test_passthrough_without_rows_is_refused_with_the_reason(self) -> None:
        with pytest.raises(RenderError, match="input rows"):
            render(request(passthrough=("title",)))

    def test_unknown_field_selection_is_refused(self) -> None:
        with pytest.raises(RenderError, match="nope"):
            render(request(fields=["nope"]))


class TestEnrich:
    def test_input_columns_keep_their_order_and_values(self) -> None:
        columns, records = parse_csv(render(request(enrich=True, rows=ROWS)))

        assert columns == ["post_id", "title", "body", "severity"]
        assert records[0]["title"] == "Leak"
        assert records[0]["body"] == "Water"

    def test_a_same_named_column_is_overwritten_not_duplicated(self) -> None:
        columns, records = parse_csv(render(request(enrich=True, rows=ROWS)))

        assert columns.count("severity") == 1
        assert records[1]["severity"] == "high"

    def test_only_the_derived_column_differs_from_the_input(self) -> None:
        _, records = parse_csv(render(request(enrich=True, rows=ROWS)))

        for rendered_row, original in zip(records, ROWS, strict=True):
            untouched = {k: v for k, v in rendered_row.items() if k != "severity"}
            assert untouched == {k: v for k, v in original.items() if k != "severity"}

    def test_fill_missing_leaves_a_filled_cell_alone(self) -> None:
        _, records = parse_csv(
            render(request(enrich=True, fill_missing=True, rows=ROWS))
        )

        assert records[0]["severity"] == "high"  # was empty → filled
        assert records[1]["severity"] == "low"  # was filled → untouched

    def test_a_row_with_no_result_keeps_whatever_the_input_held(self) -> None:
        """Enrich never blanks a cell it has nothing to write."""
        _, records = parse_csv(
            render(request(entries=[ENTRIES[1]], enrich=True, rows=ROWS))
        )

        assert records[0]["severity"] == ""  # input was empty, no result
        assert records[1]["severity"] == "high"  # input was 'low', result wins

    def test_enrich_without_rows_is_refused(self) -> None:
        with pytest.raises(RenderError, match="enrich mode"):
            render(request(enrich=True))

    def test_enrich_round_trips_a_json_array_input(self) -> None:
        payload = json.loads(render(request(enrich=True, rows=ROWS, format="json")))

        assert list(payload[0]) == ["post_id", "title", "body", "severity"]


class TestNestedShapes:
    NESTED_JOB = job_of(
        {
            "name": "facts",
            "kind": "extract",
            "pool": "cheap",
            "prompt": {"inputs": ["title"], "template": "{{ title }}"},
            "schema": {
                "properties": {
                    "tier": {"type": "string"},
                    "seats": {"type": "integer"},
                }
            },
        }
    )
    NESTED_ENTRIES: ClassVar[list[LedgerEntry]] = [
        LedgerEntry(
            key="1",
            field="facts",
            outcome="ok",
            value={"tier": "gold", "seats": 2},
            model="a",
        )
    ]

    def test_csv_refuses_a_nested_shape_naming_the_field(self) -> None:
        with pytest.raises(RenderError, match="facts"):
            render(
                RenderRequest(
                    job=self.NESTED_JOB, entries=self.NESTED_ENTRIES, format="csv"
                )
            )

    def test_jsonl_accepts_the_same_nested_shape(self) -> None:
        rendered = render(
            RenderRequest(
                job=self.NESTED_JOB, entries=self.NESTED_ENTRIES, format="jsonl"
            )
        )

        assert json.loads(rendered.strip())["facts"] == {"tier": "gold", "seats": 2}

    def test_a_single_property_shape_is_fine_in_csv(self) -> None:
        flat_job = job_of(
            {
                "name": "facts",
                "kind": "extract",
                "pool": "cheap",
                "prompt": {"inputs": ["title"], "template": "{{ title }}"},
                "schema": {"properties": {"tier": {"type": "string"}}},
            }
        )

        rendered = render(
            RenderRequest(
                job=flat_job,
                entries=[
                    LedgerEntry(key="1", field="facts", outcome="ok", value="gold")
                ],
                format="csv",
            )
        )

        assert "gold" in rendered
