"""remuda — bulk LLM inference over pools of free/cheap models.

The herd of spare horses on a cattle drive: ride one until it tires, swap to
the next. Public API surface lives here; internal module paths are not a
contract.
"""

from remuda.api import JobRunOutcome, run, run_job_dir, run_sync
from remuda.engine.plan import EngineError
from remuda.engine.progress import ProgressEvent
from remuda.engine.runner import RowResult, Runner
from remuda.errors import RemudaError
from remuda.inspection import (
    CheckReport,
    PreviewReport,
    RenderedPrompt,
    check_job_dir,
    preview_job,
)
from remuda.ledger import (
    LedgerEntry,
    RenderError,
    RenderRequest,
    Report,
    ResumeRefusedError,
    RunStore,
    SpecLock,
    render,
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
from remuda.transport import (
    CompletionRequest,
    CompletionResult,
    PermanentTransportError,
    RateLimitedError,
    TransientTransportError,
    Transport,
    TransportError,
    Usage,
)
from remuda.validate import Outcome, Verdict, validate_answer
from remuda.version import __version__

__all__ = [
    "CheckReport",
    "CompletionRequest",
    "CompletionResult",
    "DiscoverQuery",
    "EngineError",
    "ExtractSchema",
    "FieldSpec",
    "GenerateConstraints",
    "InputSpec",
    "Job",
    "JobRunOutcome",
    "LedgerEntry",
    "MapTable",
    "ModelConfig",
    "Outcome",
    "PermanentTransportError",
    "Pool",
    "PoolEntry",
    "Precondition",
    "PreviewReport",
    "ProgressEvent",
    "PromptRenderError",
    "PromptSpec",
    "Provider",
    "RateLimitedError",
    "Registry",
    "RegistryError",
    "RegistryValidationError",
    "RemudaError",
    "RenderError",
    "RenderRequest",
    "RenderedPrompt",
    "Report",
    "ResumeRefusedError",
    "RowResult",
    "RowSourceError",
    "RunStore",
    "Runner",
    "ScoreRange",
    "SpecLock",
    "SpecValidationError",
    "TransientTransportError",
    "Transport",
    "TransportError",
    "Usage",
    "Verdict",
    "__version__",
    "check_job_dir",
    "default_config_dirs",
    "load_registry",
    "preview_job",
    "read_columns",
    "read_rows",
    "render",
    "run",
    "run_job_dir",
    "run_sync",
    "validate_answer",
]
