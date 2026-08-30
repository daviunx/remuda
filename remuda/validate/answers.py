"""Per-kind answer validators (FR-2, FR-4).

A validator never repairs an answer for the model: it either accepts the
answer in its normalized form, or refuses it with feedback precise enough
that the next attempt can succeed.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

from remuda.spec.models import FieldSpec
from remuda.spec.shapes import (
    ExtractProperty,
    ExtractSchema,
    GenerateConstraints,
    MapTable,
    ScoreRange,
)
from remuda.validate.verdict import Verdict

#: Wrapping the model tends to add around a bare answer.
_STRIPPABLE = " \t\r\n\"'`*_.;:"

_FENCE = re.compile(r"```[a-zA-Z]*\n?(?P<body>.*?)```", re.DOTALL)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def validate_answer(field: FieldSpec, raw: str) -> Verdict:
    """Judge one raw model answer against the field's declared outcome.

    Raises:
        ValueError: the field's kind takes no model answer (kind 'map').
    """
    if field.kind == "classify":
        return _validate_classify(field.vocabulary, raw)
    if field.kind == "extract":
        return _validate_extract(_require(field.extract_schema, field, "schema"), raw)
    if field.kind == "generate":
        return _validate_generate(field.constraints, raw)
    if field.kind == "score":
        return _validate_score(_require(field.score_range, field, "range"), raw)
    raise ValueError(
        f"field '{field.name}' has kind '{field.kind}' and takes no model answer"
    )


def evaluate_map(field: FieldSpec, row: Mapping[str, Any]) -> Verdict:
    """Resolve a `map` field deterministically — no model is involved."""
    table = _require(field.map_table, field, "map")
    looked_up = str(row.get(table.lookup, "")).strip()
    answer = _lookup(table, looked_up)
    if answer is None:
        return Verdict.invalid(
            f"'{looked_up}' has no entry in the lookup table for column "
            f"'{table.lookup}' and no default is declared"
        )
    return Verdict.valid(answer)


def _lookup(table: MapTable, value: str) -> str | None:
    if table.case_sensitive:
        return table.table.get(value, table.default)
    folded = {key.casefold(): entry for key, entry in table.table.items()}
    return folded.get(value.casefold(), table.default)


def _validate_classify(vocabulary: tuple[str, ...], raw: str) -> Verdict:
    answer = raw.strip().strip(_STRIPPABLE).strip()
    allowed = ", ".join(vocabulary)
    if not answer:
        return Verdict.invalid(f"You answered nothing. Reply with one of: {allowed}.")
    for label in vocabulary:
        if answer.casefold() == label.casefold():
            return Verdict.valid(label)
    return Verdict.invalid(
        f"'{_excerpt(answer)}' is not one of the allowed labels. Reply with "
        f"exactly one of these and nothing else: {allowed}."
    )


def _validate_generate(constraints: GenerateConstraints | None, raw: str) -> Verdict:
    answer = raw.strip()
    if not answer:
        return Verdict.invalid("You answered nothing. Write the requested text.")
    if constraints is None:
        return Verdict.valid(answer)
    if constraints.single_line:
        answer = " ".join(answer.split())
    if constraints.min_chars is not None and len(answer) < constraints.min_chars:
        return Verdict.invalid(
            f"Your answer is {len(answer)} characters; it must be at least "
            f"{constraints.min_chars}. Write a longer answer."
        )
    if constraints.max_chars is not None and len(answer) > constraints.max_chars:
        return Verdict.invalid(
            f"Your answer is {len(answer)} characters; it must be at most "
            f"{constraints.max_chars}. Write a shorter answer."
        )
    return Verdict.valid(answer)


def _validate_score(score_range: ScoreRange, raw: str) -> Verdict:
    match = _NUMBER.search(raw)
    if match is None:
        return Verdict.invalid(
            f"'{_excerpt(raw)}' contains no number. Reply with a single number "
            f"between {score_range.minimum} and {score_range.maximum}."
        )
    number = float(match.group())
    if not score_range.minimum <= number <= score_range.maximum:
        return Verdict.invalid(
            f"{number} is outside the allowed range. Reply with a single number "
            f"between {score_range.minimum} and {score_range.maximum}."
        )
    if score_range.integer:
        if number != int(number):
            return Verdict.invalid(
                f"{number} is not a whole number. Reply with a whole number "
                f"between {score_range.minimum} and {score_range.maximum}."
            )
        return Verdict.valid(int(number))
    return Verdict.valid(number)


def _validate_extract(schema: ExtractSchema, raw: str) -> Verdict:
    payload = _parse_object(raw)
    if payload is None:
        return Verdict.invalid(
            "Your answer is not a JSON object. Reply with a single JSON object "
            "with these keys and nothing else: " + ", ".join(schema.properties) + "."
        )
    extracted: dict[str, Any] = {}
    for name, declared in schema.properties.items():
        if name not in payload or payload[name] is None:
            if declared.required:
                return Verdict.invalid(
                    f"Your JSON object is missing the required key '{name}'. "
                    "Include every required key: "
                    + ", ".join(
                        key for key, prop in schema.properties.items() if prop.required
                    )
                    + "."
                )
            continue
        coerced = _coerce(declared, payload[name])
        if coerced.is_valid is False:
            return Verdict.invalid(f"Key '{name}': {coerced.repair}")
        extracted[name] = coerced.value
    return Verdict.valid(extracted)


def _coerce(declared: ExtractProperty, value: Any) -> Verdict:
    if declared.type == "enum":
        return _coerce_enum(declared.values or (), value)
    if declared.type == "boolean":
        return _coerce_boolean(value)
    if declared.type in {"number", "integer"}:
        return _coerce_number(value, is_whole=declared.type == "integer")
    text = str(value).strip()
    if not text:
        return Verdict.invalid("the value is empty.")
    return Verdict.valid(text)


def _coerce_enum(allowed: tuple[str, ...], value: Any) -> Verdict:
    for candidate in allowed:
        if str(value).strip().casefold() == candidate.casefold():
            return Verdict.valid(candidate)
    return Verdict.invalid(
        f"'{_excerpt(str(value))}' is not allowed. Use one of: "
        + ", ".join(allowed)
        + "."
    )


def _coerce_boolean(value: Any) -> Verdict:
    if isinstance(value, bool):
        return Verdict.valid(value)
    folded = str(value).strip().casefold()
    if folded in {"true", "yes", "1"}:
        return Verdict.valid(True)
    if folded in {"false", "no", "0"}:
        return Verdict.valid(False)
    return Verdict.invalid(f"'{_excerpt(str(value))}' is not true or false.")


def _coerce_number(value: Any, is_whole: bool) -> Verdict:
    match = _NUMBER.search(str(value))
    if match is None:
        return Verdict.invalid(f"'{_excerpt(str(value))}' is not a number.")
    number = float(match.group())
    if not is_whole:
        return Verdict.valid(number)
    if number != int(number):
        return Verdict.invalid(f"{number} is not a whole number.")
    return Verdict.valid(int(number))


def _parse_object(raw: str) -> dict[str, Any] | None:
    candidates = [raw.strip()]
    fenced = _FENCE.search(raw)
    if fenced is not None:
        candidates.insert(0, fenced.group("body").strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _require[T](declared: T | None, field: FieldSpec, noun: str) -> T:
    if declared is None:  # unreachable: the spec models enforce this at load
        raise ValueError(f"field '{field.name}' declares no '{noun}'")
    return declared


def _excerpt(value: str, limit: int = 60) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"
