"""Job and field models — the versionable definition of a bulk job (FR-1, FR-2).

Every rule that can be decided from the specification alone is enforced here,
so a host embedding remuda programmatically (FR-7) is refused by the same
boundary a job directory is.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal, NoReturn, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from remuda.spec.errors import PromptRenderError
from remuda.spec.prompt import render_prompt, template_variables
from remuda.spec.shapes import (
    ExtractSchema,
    GenerateConstraints,
    MapTable,
    Precondition,
    ScoreRange,
)

FieldKind = Literal["classify", "extract", "generate", "score", "map"]
FIELD_KINDS: Final[tuple[str, ...]] = get_args(FieldKind)

InputFormat = Literal["auto", "csv", "jsonl", "json"]

#: Kinds whose answer comes from a model, and therefore need a prompt + pool.
_MODEL_BACKED_KINDS: Final[frozenset[str]] = frozenset(
    {"classify", "extract", "generate", "score"}
)

#: Declaration each kind requires, by the name it carries in `job.yaml`.
_REQUIRED_DECLARATION: Final[dict[str, str]] = {
    "classify": "vocabulary",
    "extract": "schema",
    "score": "range",
    "map": "map",
}


def _duplicates(values: Sequence[str]) -> list[str]:
    """Return the values appearing more than once, sorted."""
    return sorted(value for value, count in Counter(values).items() if count > 1)


class InputSpec(BaseModel):
    """Where the rows come from and which column identifies them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(description="Path relative to the job directory.")
    key: str = Field(description="Column holding each row's unique key.")
    format: InputFormat = "auto"


class PromptSpec(BaseModel):
    """A prompt template plus the allowlist of inputs it may read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template: str
    inputs: tuple[str, ...] = ()
    system: str | None = None

    @model_validator(mode="after")
    def _template_is_not_blank(self) -> Self:
        if not self.template.strip():
            raise ValueError("prompt template is empty")
        return self


class ExecutionSpec(BaseModel):
    """How the job is run — consumed by the engine, declared with the job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    records_per_call: int = Field(default=1, ge=1)
    attempts_per_model: int = Field(default=2, ge=1)
    workers: int = Field(default=4, ge=1)


class FieldSpec(BaseModel):
    """One named field derived from each input row."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    name: str
    kind: FieldKind
    pool: str | None = None
    prompt: PromptSpec | None = None
    vocabulary: tuple[str, ...] = ()
    vocabulary_file: str | None = Field(
        default=None,
        description="Path relative to the job directory; loaded into 'vocabulary'.",
    )
    extract_schema: ExtractSchema | None = Field(default=None, alias="schema")
    constraints: GenerateConstraints | None = None
    score_range: ScoreRange | None = Field(default=None, alias="range")
    map_table: MapTable | None = Field(default=None, alias="map")
    when: Precondition | None = None
    depends_on: str | None = None

    @model_validator(mode="after")
    def _declaration_matches_kind(self) -> Self:
        self._check_model_backed_requirements()
        self._check_kind_specific_declaration()
        self._check_template_inputs()
        return self

    def render(self, row: Mapping[str, Any]) -> str:
        """Render this field's prompt against `row`, seeing declared inputs only.

        Raises:
            PromptRenderError: the field has no prompt, or the row lacks a
                declared input.
        """
        if self.prompt is None:
            raise PromptRenderError(
                f"field '{self.name}' has kind '{self.kind}' and declares no "
                "prompt to render"
            )
        return render_prompt(self.prompt.template, self.prompt.inputs, row)

    # -- validation helpers ------------------------------------------------

    def _refuse(self, message: str) -> NoReturn:
        raise ValueError(f"field '{self.name}': {message}")

    def _check_model_backed_requirements(self) -> None:
        if self.kind in _MODEL_BACKED_KINDS:
            if self.prompt is None:
                self._refuse(f"kind '{self.kind}' needs a 'prompt' declaration")
            if not self.pool:
                self._refuse(f"kind '{self.kind}' needs a 'pool' to answer from")
            return
        if self.prompt is not None or self.pool:
            self._refuse(
                "kind 'map' is a deterministic lookup — it must declare neither "
                "'prompt' nor 'pool'"
            )

    def _check_kind_specific_declaration(self) -> None:
        declared = {
            "vocabulary": bool(self.vocabulary),
            "schema": self.extract_schema is not None,
            "range": self.score_range is not None,
            "map": self.map_table is not None,
        }
        required = _REQUIRED_DECLARATION.get(self.kind)
        if required is not None and not declared[required]:
            self._refuse(f"kind '{self.kind}' needs a '{required}' declaration")
        for key, is_declared in declared.items():
            if is_declared and key != required:
                self._refuse(
                    f"'{key}' is not meaningful for kind '{self.kind}' — "
                    "remove it or change the kind"
                )
        if self.constraints is not None and self.kind != "generate":
            self._refuse(
                f"'constraints' is only meaningful for kind 'generate', "
                f"not '{self.kind}'"
            )
        if self.kind == "classify":
            self._check_vocabulary()

    def _check_vocabulary(self) -> None:
        if any(not label.strip() for label in self.vocabulary):
            self._refuse("vocabulary contains a blank label")
        duplicated = _duplicates(self.vocabulary)
        if duplicated:
            self._refuse(
                "vocabulary contains duplicate label(s): " + ", ".join(duplicated)
            )

    def _check_template_inputs(self) -> None:
        if self.prompt is None:
            return
        declared = set(self.prompt.inputs)
        for template in (self.prompt.template, self.prompt.system or ""):
            undeclared = sorted(template_variables(template) - declared)
            if not undeclared:
                continue
            self._refuse(
                "prompt template references undeclared input(s) "
                + ", ".join(f"'{name}'" for name in undeclared)
                + " — add them to prompt.inputs or remove them from the "
                "template (declared inputs: "
                + (", ".join(self.prompt.inputs) or "none")
                + ")"
            )


class Job(BaseModel):
    """A complete, versionable bulk job definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    input: InputSpec
    fields: tuple[FieldSpec, ...]
    description: str | None = None
    execution: ExecutionSpec = Field(default_factory=ExecutionSpec)

    @classmethod
    def from_dir(cls, job_dir: Path | str) -> "Job":
        """Load and lint a job directory.

        Raises:
            SpecValidationError: the directory or its `job.yaml` is
                misdeclared; every defect found is named in the message.
        """
        # Imported here: the loader imports this module, so a module-level
        # import would be circular.
        from remuda.spec.loader import load_job_dir  # noqa: PLC0415

        return load_job_dir(Path(job_dir))

    def field(self, name: str) -> FieldSpec:
        """Return the field declared under `name`.

        Raises:
            KeyError: no such field is declared.
        """
        for declared in self.fields:
            if declared.name == name:
                return declared
        raise KeyError(
            f"job '{self.name}' declares no field '{name}' "
            f"(declared: {', '.join(self.field_names)})"
        )

    @property
    def field_names(self) -> tuple[str, ...]:
        """Every declared field name, in declaration order."""
        return tuple(field.name for field in self.fields)

    @model_validator(mode="after")
    def _fields_are_coherent(self) -> Self:
        if not self.fields:
            raise ValueError("job declares no fields")
        self._check_unique_names()
        self._check_dependencies()
        self._check_precondition_sources()
        return self

    def _check_unique_names(self) -> None:
        duplicated = _duplicates(self.field_names)
        if duplicated:
            raise ValueError("duplicate field name(s): " + ", ".join(duplicated))

    def _check_dependencies(self) -> None:
        by_name = {field.name: field for field in self.fields}
        for field in self.fields:
            target_name = field.depends_on
            if target_name is None:
                continue
            if target_name == field.name:
                raise ValueError(f"field '{field.name}': depends_on itself")
            target = by_name.get(target_name)
            if target is None:
                raise ValueError(
                    f"field '{field.name}': depends_on unknown field "
                    f"'{target_name}' (declared: {', '.join(self.field_names)})"
                )
            if target.depends_on is not None:
                raise ValueError(
                    f"field '{field.name}': depends_on '{target_name}', which "
                    f"itself depends on '{target.depends_on}' — dependency "
                    "chains are not supported, only one level"
                )

    def _check_precondition_sources(self) -> None:
        names = set(self.field_names)
        for field in self.fields:
            if field.when is None:
                continue
            source = field.when.source
            if source in names and source != field.depends_on:
                raise ValueError(
                    f"field '{field.name}': precondition source '{source}' is "
                    f"another declared field — add depends_on: {source} so it "
                    "is derived first"
                )


def declared_input_columns(fields: Sequence[FieldSpec]) -> tuple[str, ...]:
    """Every input column the given fields read, de-duplicated and sorted."""
    columns: set[str] = set()
    for field in fields:
        if field.prompt is not None:
            columns.update(field.prompt.inputs)
        if field.map_table is not None:
            columns.add(field.map_table.lookup)
    return tuple(sorted(columns))
