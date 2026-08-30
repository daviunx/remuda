"""remuda — bulk LLM inference over pools of free/cheap models.

The herd of spare horses on a cattle drive: ride one until it tires, swap to
the next. Public API surface lives here; internal module paths are not a
contract.
"""

from remuda.errors import RemudaError
from remuda.inspection import (
    CheckReport,
    PreviewReport,
    RenderedPrompt,
    check_job_dir,
    preview_job,
)
from remuda.registry import (
    DiscoverQuery,
    ModelConfig,
    Pool,
    PoolEntry,
    Provider,
    Registry,
    RegistryError,
    RegistryValidationError,
    default_config_dirs,
    load_registry,
)
from remuda.rows import RowSourceError, read_columns, read_rows
from remuda.spec import (
    ExtractSchema,
    FieldSpec,
    GenerateConstraints,
    InputSpec,
    Job,
    MapTable,
    Precondition,
    PromptRenderError,
    PromptSpec,
    ScoreRange,
    SpecValidationError,
)

__all__ = [
    "CheckReport",
    "DiscoverQuery",
    "ExtractSchema",
    "FieldSpec",
    "GenerateConstraints",
    "InputSpec",
    "Job",
    "MapTable",
    "ModelConfig",
    "Pool",
    "PoolEntry",
    "Precondition",
    "PreviewReport",
    "PromptRenderError",
    "PromptSpec",
    "Provider",
    "Registry",
    "RegistryError",
    "RegistryValidationError",
    "RemudaError",
    "RenderedPrompt",
    "RowSourceError",
    "ScoreRange",
    "SpecValidationError",
    "check_job_dir",
    "default_config_dirs",
    "load_registry",
    "preview_job",
    "read_columns",
    "read_rows",
]

__version__ = "0.1.0"
