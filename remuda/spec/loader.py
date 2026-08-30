"""Loading a job directory from disk (FR-1).

The loader owns everything the models cannot decide on their own: reading
`job.yaml`, resolving the files a field points at (vocabulary, map table),
and turning Pydantic's validation report into defect lines that name the
field an operator has to fix.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from remuda.spec.errors import SpecValidationError
from remuda.spec.models import FIELD_KINDS, Job

JOB_FILENAME = "job.yaml"

#: Alternative spellings accepted for the job file, in lookup order.
_JOB_FILENAMES: tuple[str, ...] = (JOB_FILENAME, "job.yml")


def load_job_dir(job_dir: Path) -> Job:
    """Load and lint the job directory at `job_dir`.

    Raises:
        SpecValidationError: the directory, its job file, or the declaration
            inside it is defective. Every defect found is named.
    """
    directory = Path(job_dir)
    if not directory.is_dir():
        raise SpecValidationError(
            directory, ["job directory does not exist (or is not a directory)"]
        )

    spec_path = _find_job_file(directory)
    raw = _read_mapping(spec_path)

    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, list) or not fields_raw:
        raise SpecValidationError(
            spec_path,
            [
                "'fields' must be a non-empty list of field declarations "
                "(each with a name and a kind)"
            ],
        )

    defects = _check_field_kinds(fields_raw)
    defects.extend(_resolve_field_files(directory, fields_raw))
    if defects:
        raise SpecValidationError(spec_path, defects)

    try:
        return Job.model_validate(raw)
    except ValidationError as error:
        raise SpecValidationError(spec_path, _describe(error, fields_raw)) from error


def load_vocabulary(path: Path) -> tuple[str, ...]:
    """Read a vocabulary file: one label per line, `#` comments ignored."""
    labels = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        label = line.strip()
        if label and not label.startswith("#"):
            labels.append(label)
    return tuple(labels)


# -- internals -------------------------------------------------------------


def _find_job_file(directory: Path) -> Path:
    for filename in _JOB_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    raise SpecValidationError(
        directory,
        [f"no {JOB_FILENAME} found in the job directory"],
    )


def _read_mapping(spec_path: Path) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise SpecValidationError(spec_path, [f"YAML is not parseable: {error}"]) from (
            error
        )
    if parsed is None:
        raise SpecValidationError(spec_path, ["job file is empty"])
    if not isinstance(parsed, dict):
        raise SpecValidationError(
            spec_path,
            [f"job file must contain a mapping, found {type(parsed).__name__}"],
        )
    return parsed


def _check_field_kinds(fields_raw: Sequence[Any]) -> list[str]:
    """Name unknown kinds before Pydantic reports them as a Literal mismatch."""
    accepted = ", ".join(sorted(FIELD_KINDS))
    defects: list[str] = []
    for index, entry in enumerate(fields_raw):
        if not isinstance(entry, dict):
            defects.append(
                f"fields[{index}]: expected a field declaration mapping, "
                f"found {type(entry).__name__}"
            )
            continue
        label = _field_label(entry, index)
        kind = entry.get("kind")
        if kind is None:
            defects.append(f"{label}: no 'kind' declared — accepted: {accepted}")
        elif kind not in FIELD_KINDS:
            defects.append(
                f"{label}: unknown kind '{kind}' — accepted kinds: {accepted}"
            )
    return defects


def _resolve_field_files(directory: Path, fields_raw: Sequence[Any]) -> list[str]:
    """Load the files a field points at into the declaration itself."""
    defects: list[str] = []
    for index, entry in enumerate(fields_raw):
        if not isinstance(entry, dict):
            continue
        label = _field_label(entry, index)
        defects.extend(_resolve_vocabulary(directory, entry, label))
        defects.extend(_resolve_map_table(directory, entry, label))
    return defects


def _resolve_vocabulary(
    directory: Path, entry: dict[str, Any], label: str
) -> list[str]:
    declared = entry.get("vocabulary_file")
    if not isinstance(declared, str) or entry.get("vocabulary") is not None:
        return []
    path = directory / declared
    if not path.is_file():
        return [f"{label}: vocabulary file '{declared}' not found (looked for {path})"]
    labels = load_vocabulary(path)
    if not labels:
        return [f"{label}: vocabulary file '{declared}' declares no labels"]
    entry["vocabulary"] = list(labels)
    return []


def _resolve_map_table(directory: Path, entry: dict[str, Any], label: str) -> list[str]:
    table = entry.get("map")
    if not isinstance(table, dict):
        return []
    declared = table.get("table_file")
    if not isinstance(declared, str) or table.get("table") is not None:
        return []
    path = directory / declared
    if not path.is_file():
        return [f"{label}: map table file '{declared}' not found (looked for {path})"]
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        return [f"{label}: map table file '{declared}' is not parseable: {error}"]
    if not isinstance(loaded, dict) or not loaded:
        return [
            f"{label}: map table file '{declared}' must contain a non-empty "
            "mapping of lookup value to answer"
        ]
    table["table"] = {str(key): str(value) for key, value in loaded.items()}
    return []


def _field_label(entry: dict[str, Any], index: int) -> str:
    name = entry.get("name")
    if isinstance(name, str) and name:
        return f"field '{name}'"
    return f"fields[{index}]"


def _describe(error: ValidationError, fields_raw: Sequence[Any]) -> list[str]:
    """Turn a Pydantic report into defect lines that name the field."""
    defects: list[str] = []
    for item in error.errors():
        message = str(item["msg"]).removeprefix("Value error, ")
        if message.startswith("field '"):
            # Raised by a FieldSpec validator, already self-describing.
            defects.append(message)
            continue
        location = _describe_location(item["loc"], fields_raw)
        defects.append(f"{location}: {message}" if location else message)
    return sorted(set(defects))


def _describe_location(loc: Sequence[Any], fields_raw: Sequence[Any]) -> str:
    if not loc:
        return ""
    parts = list(loc)
    prefix = ""
    if len(parts) >= 2 and parts[0] == "fields" and isinstance(parts[1], int):
        index = parts[1]
        entry = fields_raw[index] if index < len(fields_raw) else None
        prefix = _field_label(entry if isinstance(entry, dict) else {}, index)
        parts = parts[2:]
        if not parts:
            return prefix
        return f"{prefix} → {'.'.join(str(part) for part in parts)}"
    return ".".join(str(part) for part in parts)
