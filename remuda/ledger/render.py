"""Rendering a finished run's results (FR-10).

Everything here reads the ledger — no model is ever called. Enrich mode is
first class: the input file comes back with its columns in their original
order and only the derived columns added or updated.
"""

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

from remuda.errors import RemudaError
from remuda.ledger.models import LedgerEntry
from remuda.spec.models import Job

RenderFormat = Literal["csv", "jsonl", "json"]

#: Passthrough selector meaning "every input column".
ALL_COLUMNS = "*"


class RenderError(RemudaError):
    """Results could not be rendered into the requested shape."""


@dataclass(frozen=True)
class RenderRequest:
    """What to render, in which shape, carrying which input columns."""

    job: Job
    entries: Sequence[LedgerEntry]
    format: RenderFormat = "csv"
    fields: Sequence[str] | None = None
    passthrough: Sequence[str] = dataclass_field(default_factory=tuple)
    enrich: bool = False
    fill_missing: bool = False
    rows: Sequence[Mapping[str, Any]] | None = None


def render(request: RenderRequest) -> str:
    """Render the run's results.

    Raises:
        RenderError: the request needs input rows it was not given, names an
            unknown column, or asks CSV for a nested shape.
    """
    fields = _fields(request)
    if request.format == "csv":
        _refuse_nested(request.job, fields)
    derived = _derived(request.entries)
    records = (
        _enriched(request, fields, derived)
        if request.enrich
        else _projected(request, fields, derived)
    )
    return _emit(records, request.format)


# -- record building -------------------------------------------------------


def _fields(request: RenderRequest) -> tuple[str, ...]:
    if request.fields is None:
        return request.job.field_names
    unknown = [name for name in request.fields if name not in request.job.field_names]
    if unknown:
        raise RenderError(
            "unknown field(s) "
            + ", ".join(f"'{name}'" for name in unknown)
            + f" — this job declares: {', '.join(request.job.field_names)}"
        )
    return tuple(request.fields)


def _derived(entries: Sequence[LedgerEntry]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entry.outcome == "ok":
            values.setdefault(entry.key, {})[entry.field] = entry.value
    return values


def _enriched(
    request: RenderRequest,
    fields: Sequence[str],
    derived: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = _require_rows(request, "enrich mode")
    key_column = request.job.input.key
    records: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)  # input column order preserved exactly
        answers = derived.get(str(row.get(key_column, "")), {})
        for name in fields:
            if name not in answers:
                record.setdefault(name, "")
                continue
            existing = str(record.get(name, "")).strip()
            if request.fill_missing and existing:
                continue
            record[name] = answers[name]
        records.append(record)
    return records


def _projected(
    request: RenderRequest,
    fields: Sequence[str],
    derived: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    key_column = request.job.input.key
    carried = _passthrough_columns(request)
    rows_by_key = _rows_by_key(request) if carried else {}
    records: list[dict[str, Any]] = []
    for key in _key_order(request):
        record: dict[str, Any] = {key_column: key}
        source = rows_by_key.get(key, {})
        for column in carried:
            if column != key_column:
                record[column] = source.get(column, "")
        answers = derived.get(key, {})
        for name in fields:
            record[name] = answers.get(name, "")
        records.append(record)
    return records


def _key_order(request: RenderRequest) -> list[str]:
    """Input order when rows are available; ledger order otherwise."""
    if request.rows is not None:
        key_column = request.job.input.key
        return [str(row.get(key_column, "")) for row in request.rows]
    seen: list[str] = []
    for entry in request.entries:
        if entry.key not in seen:
            seen.append(entry.key)
    return seen


def _passthrough_columns(request: RenderRequest) -> tuple[str, ...]:
    if not request.passthrough:
        return ()
    rows = _require_rows(request, "passthrough columns")
    available = tuple(rows[0]) if rows else ()
    if ALL_COLUMNS in request.passthrough:
        return available
    unknown = [name for name in request.passthrough if name not in available]
    if unknown:
        raise RenderError(
            "unknown passthrough column(s) "
            + ", ".join(f"'{name}'" for name in unknown)
            + f" — the input has: {', '.join(available)}"
        )
    return tuple(request.passthrough)


def _rows_by_key(request: RenderRequest) -> dict[str, Mapping[str, Any]]:
    key_column = request.job.input.key
    rows = _require_rows(request, "passthrough columns")
    return {str(row.get(key_column, "")): row for row in rows}


def _require_rows(request: RenderRequest, purpose: str) -> Sequence[Mapping[str, Any]]:
    if request.rows is None:
        raise RenderError(
            f"{purpose} needs the input rows — render with the job's input "
            "file available"
        )
    return request.rows


def _refuse_nested(job: Job, fields: Sequence[str]) -> None:
    for name in fields:
        schema = job.field(name).extract_schema
        if schema is not None and schema.is_nested:
            raise RenderError(
                f"field '{name}' has a nested shape ({', '.join(schema.properties)}) "
                "and cannot be written to a CSV cell — render as jsonl or json, "
                f"or drop '{name}' from the selection"
            )


# -- emitting --------------------------------------------------------------


def _emit(records: Sequence[Mapping[str, Any]], render_format: RenderFormat) -> str:
    if render_format == "jsonl":
        return "".join(json.dumps(record, default=str) + "\n" for record in records)
    if render_format == "json":
        return json.dumps(list(records), indent=2, default=str) + "\n"
    return _emit_csv(records)


def _emit_csv(records: Sequence[Mapping[str, Any]]) -> str:
    columns: list[str] = []
    for record in records:
        for column in record:
            if column not in columns:
                columns.append(column)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow({column: _cell(record.get(column)) for column in columns})
    return buffer.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        raise RenderError(
            f"a nested value cannot be written to a CSV cell: {value!r} — "
            "render as jsonl or json"
        )
    return str(value)
