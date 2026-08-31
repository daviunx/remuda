"""Per-kind outcome declarations and preconditions (FR-2).

Each field kind declares the shape its answer must take. These models carry
the declaration only; the validators that enforce an answer against them
live in the `validate` module.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PropertyType = Literal["string", "number", "integer", "boolean", "enum"]


class ExtractProperty(BaseModel):
    """One property of an `extract` field's declared shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: PropertyType = "string"
    values: tuple[str, ...] | None = Field(
        default=None, description="Allowed values; required for type 'enum'."
    )
    required: bool = True
    description: str | None = None

    @model_validator(mode="after")
    def _values_belong_to_enums(self) -> Self:
        if self.type == "enum" and not self.values:
            raise ValueError("property of type 'enum' must declare 'values'")
        if self.type != "enum" and self.values is not None:
            raise ValueError(
                f"property of type '{self.type}' must not declare 'values' "
                "(only 'enum' properties have a closed value set)"
            )
        return self


class ExtractSchema(BaseModel):
    """The declared shape of an `extract` field's answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    properties: dict[str, ExtractProperty]

    @model_validator(mode="after")
    def _at_least_one_property(self) -> Self:
        if not self.properties:
            raise ValueError("schema must declare at least one property")
        return self

    @property
    def is_nested(self) -> bool:
        """True when the shape cannot be rendered into a single CSV cell."""
        return len(self.properties) > 1


class GenerateConstraints(BaseModel):
    """Declared constraints on a `generate` field's free text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_chars: int | None = Field(default=None, ge=0)
    max_chars: int | None = Field(default=None, ge=1)
    single_line: bool = False

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> Self:
        if (
            self.min_chars is not None
            and self.max_chars is not None
            and self.min_chars > self.max_chars
        ):
            raise ValueError(
                f"min_chars ({self.min_chars}) exceeds max_chars ({self.max_chars})"
            )
        return self


class ScoreRange(BaseModel):
    """The declared numeric range of a `score` field's answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: float
    maximum: float
    integer: bool = False

    @model_validator(mode="after")
    def _range_is_ordered(self) -> Self:
        if self.maximum <= self.minimum:
            raise ValueError(
                f"maximum ({self.maximum}) must be greater than "
                f"minimum ({self.minimum})"
            )
        return self


class MapTable(BaseModel):
    """A deterministic lookup — a `map` field never calls a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lookup: str = Field(description="Input column whose value is looked up.")
    table: dict[str, str] = Field(default_factory=dict)
    table_file: str | None = Field(
        default=None,
        description="Path relative to the job directory; loaded into 'table'.",
    )
    default: str | None = None
    case_sensitive: bool = False

    @model_validator(mode="after")
    def _table_is_populated(self) -> Self:
        if not self.table:
            raise ValueError(
                "map table is empty — declare 'table' inline or point "
                "'table_file' at a lookup file"
            )
        return self


class Precondition(BaseModel):
    """A `when:` guard — the field is skipped (not failed) when it fails."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(
        description="Input column or the field named by depends_on.",
    )
    not_empty: bool = True
    min_length: int | None = Field(default=None, ge=0)
    equals: str | None = None
    one_of: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _one_value_test_at_most(self) -> Self:
        if self.equals is not None and self.one_of is not None:
            raise ValueError("declare either 'equals' or 'one_of', never both")
        return self
