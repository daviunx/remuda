"""Job specification: models, loading and lint (FR-1, FR-2)."""

from remuda.spec.errors import PromptRenderError, SpecValidationError
from remuda.spec.lint import PLACEHOLDER, lint_against_columns, lint_placeholders
from remuda.spec.loader import JOB_FILENAME, load_job_dir, load_vocabulary
from remuda.spec.models import (
    FIELD_KINDS,
    ExecutionSpec,
    FieldKind,
    FieldSpec,
    InputFormat,
    InputSpec,
    Job,
    PromptSpec,
    declared_input_columns,
)
from remuda.spec.prompt import render_prompt, template_variables
from remuda.spec.shapes import (
    ExtractProperty,
    ExtractSchema,
    GenerateConstraints,
    MapTable,
    Precondition,
    PropertyType,
    ScoreRange,
)

__all__ = [
    "FIELD_KINDS",
    "JOB_FILENAME",
    "PLACEHOLDER",
    "ExecutionSpec",
    "ExtractProperty",
    "ExtractSchema",
    "FieldKind",
    "FieldSpec",
    "GenerateConstraints",
    "InputFormat",
    "InputSpec",
    "Job",
    "MapTable",
    "Precondition",
    "PromptRenderError",
    "PromptSpec",
    "PropertyType",
    "ScoreRange",
    "SpecValidationError",
    "declared_input_columns",
    "lint_against_columns",
    "lint_placeholders",
    "load_job_dir",
    "load_vocabulary",
    "render_prompt",
    "template_variables",
]
