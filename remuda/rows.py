"""Reading input rows from a file (CSV, JSONL, or a JSON array).

Rows are plain dicts on purpose: nothing above this module knows a consumer's
column vocabulary, so any extra column rides through untouched
(`development/library-design.md`).
"""

import csv
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from remuda.errors import RemudaError

RowFormat = Literal["auto", "csv", "jsonl", "json"]

Row = dict[str, Any]

_SUFFIX_FORMATS: dict[str, RowFormat] = {
    ".csv": "csv",
    ".tsv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "json",
}


class RowSourceError(RemudaError):
    """An input file could not be read as rows."""


def resolve_format(path: Path, declared: RowFormat = "auto") -> RowFormat:
    """Resolve the concrete format of `path`, from its suffix when 'auto'."""
    if declared != "auto":
        return declared
    resolved = _SUFFIX_FORMATS.get(path.suffix.lower())
    if resolved is None:
        raise RowSourceError(
            f"cannot infer the format of '{path.name}' from its suffix — "
            "declare input.format as csv, jsonl or json"
        )
    return resolved


def read_columns(path: Path, declared: RowFormat = "auto") -> tuple[str, ...]:
    """Return the column names of the input file, in file order."""
    _require_file(path)
    row_format = resolve_format(path, declared)
    if row_format == "csv":
        with _open(path) as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
        if header is None:
            raise RowSourceError(f"input file '{path}' has no header row")
        return tuple(header)
    for row in read_rows(path, declared, limit=1):
        return tuple(row)
    return ()


def read_rows(
    path: Path, declared: RowFormat = "auto", limit: int | None = None
) -> list[Row]:
    """Read up to `limit` rows (all of them when `limit` is None)."""
    if limit is not None and limit < 0:
        raise RowSourceError("row limit cannot be negative")
    return list(_iter_rows(path, declared, limit))


def row_key(row: Mapping[str, Any], key_column: str) -> str:
    """Return a row's key, refusing a row that has none.

    Raises:
        RowSourceError: the key column is absent or empty on this row.
    """
    value = row.get(key_column)
    if value is None or str(value).strip() == "":
        raise RowSourceError(
            f"row has no value in key column '{key_column}' — every row needs "
            "a unique key"
        )
    return str(value)


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise RowSourceError(f"input file '{path}' does not exist")


def _iter_rows(path: Path, declared: RowFormat, limit: int | None) -> Iterator[Row]:
    _require_file(path)
    row_format = resolve_format(path, declared)
    if row_format == "csv":
        yield from _take(_iter_csv(path), limit)
    elif row_format == "jsonl":
        yield from _take(_iter_jsonl(path), limit)
    else:
        yield from _take(_iter_json_array(path), limit)


def _take(rows: Iterator[Row], limit: int | None) -> Iterator[Row]:
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            return
        yield row


def _iter_csv(path: Path) -> Iterator[Row]:
    with _open(path) as handle:
        for row in csv.DictReader(handle):
            yield dict(row)


def _iter_jsonl(path: Path) -> Iterator[Row]:
    # Framed on the protocol's real terminator, never str.splitlines()
    # (development/python.md — Wire Format Parsing).
    for number, line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), start=1
    ):
        if not line.strip():
            continue
        yield _as_row(_parse_json(line, path, f"line {number}"), path, f"line {number}")


def _iter_json_array(path: Path) -> Iterator[Row]:
    payload = _parse_json(path.read_text(encoding="utf-8"), path, "file")
    if not isinstance(payload, Sequence) or isinstance(payload, str | bytes):
        raise RowSourceError(f"input file '{path}' must contain a JSON array of rows")
    for index, entry in enumerate(payload):
        yield _as_row(entry, path, f"item {index}")


def _parse_json(text: str, path: Path, where: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RowSourceError(f"input file '{path}' {where} is not JSON: {error}") from (
            error
        )


def _as_row(value: Any, path: Path, where: str) -> Row:
    if not isinstance(value, dict):
        raise RowSourceError(
            f"input file '{path}' {where} is not an object — every row must be "
            "a mapping of column to value"
        )
    return {str(key): entry for key, entry in value.items()}


def _open(path: Path) -> Any:
    return path.open(encoding="utf-8", newline="")
