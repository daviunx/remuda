"""Reading input rows from CSV, JSONL and JSON-array files (FR-10 input side)."""

import json
from pathlib import Path

import pytest

from remuda.rows import (
    RowSourceError,
    read_columns,
    read_rows,
    resolve_format,
    row_key,
)

CSV = "id,title,body\n1,Leak,Water\n2,Noise,Loud\n"
JSONL = '{"id": "1", "title": "Leak"}\n\n{"id": "2", "title": "Noise"}\n'
JSON_ARRAY = json.dumps([{"id": "1", "title": "Leak"}, {"id": "2", "title": "Noise"}])


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class TestReadRows:
    def test_csv_rows_carry_every_column(self, tmp_path: Path) -> None:
        rows = read_rows(write(tmp_path, "in.csv", CSV))

        assert rows == [
            {"id": "1", "title": "Leak", "body": "Water"},
            {"id": "2", "title": "Noise", "body": "Loud"},
        ]

    def test_jsonl_skips_blank_lines(self, tmp_path: Path) -> None:
        rows = read_rows(write(tmp_path, "in.jsonl", JSONL))

        assert [row["id"] for row in rows] == ["1", "2"]

    def test_json_array_round_trips(self, tmp_path: Path) -> None:
        rows = read_rows(write(tmp_path, "in.json", JSON_ARRAY))

        assert [row["title"] for row in rows] == ["Leak", "Noise"]

    def test_limit_stops_early(self, tmp_path: Path) -> None:
        rows = read_rows(write(tmp_path, "in.csv", CSV), limit=1)

        assert len(rows) == 1

    def test_missing_file_is_refused_by_name(self, tmp_path: Path) -> None:
        with pytest.raises(RowSourceError) as exc_info:
            read_rows(tmp_path / "absent.csv")

        assert "absent.csv" in str(exc_info.value)

    def test_json_array_of_scalars_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, "in.json", json.dumps(["a", "b"]))

        with pytest.raises(RowSourceError) as exc_info:
            read_rows(path)

        assert "mapping" in str(exc_info.value)


class TestColumns:
    def test_csv_header_order_is_preserved(self, tmp_path: Path) -> None:
        assert read_columns(write(tmp_path, "in.csv", CSV)) == ("id", "title", "body")

    def test_jsonl_columns_come_from_the_first_row(self, tmp_path: Path) -> None:
        assert read_columns(write(tmp_path, "in.jsonl", JSONL)) == ("id", "title")


class TestFormatResolution:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [("a.csv", "csv"), ("a.jsonl", "jsonl"), ("a.json", "json")],
    )
    def test_format_is_inferred_from_the_suffix(
        self, filename: str, expected: str
    ) -> None:
        assert resolve_format(Path(filename)) == expected

    def test_unknown_suffix_asks_for_an_explicit_format(self) -> None:
        with pytest.raises(RowSourceError) as exc_info:
            resolve_format(Path("rows.dat"))

        assert "input.format" in str(exc_info.value)

    def test_declared_format_overrides_the_suffix(self) -> None:
        assert resolve_format(Path("rows.dat"), "jsonl") == "jsonl"


class TestRowKey:
    def test_key_is_stringified(self) -> None:
        assert row_key({"id": 7}, "id") == "7"

    def test_empty_key_is_refused(self) -> None:
        with pytest.raises(RowSourceError) as exc_info:
            row_key({"id": "  "}, "id")

        assert "id" in str(exc_info.value)
