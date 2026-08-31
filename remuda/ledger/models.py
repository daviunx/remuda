"""What a run persists: the lock, the ledger lines, and the report (FR-5, FR-6)."""

from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from remuda.validate.verdict import Outcome


def _now() -> datetime:
    return datetime.now(UTC)


class SpecLock(BaseModel):
    """Fingerprints the run's inputs so a resume can prove it still applies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    remuda_version: str
    job_name: str
    job_hash: str
    input_hash: str
    input_path: str | None = None
    created_at: datetime = Field(default_factory=_now)

    def mismatches(self, other: "SpecLock") -> list[str]:
        """Return the fingerprints that differ, in operator-facing words."""
        differences: list[str] = []
        if self.job_hash != other.job_hash:
            differences.append("the job definition changed since this run started")
        if self.input_hash != other.input_hash:
            differences.append("the input file changed since this run started")
        return differences


class LedgerEntry(BaseModel):
    """One decided (row, field) result. One JSONL line, appended once."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    key: str
    field: str
    outcome: Outcome
    value: Any = None
    model: str | None = None
    attempts: int = 0
    rejects: tuple[str, ...] = ()
    reason: str | None = None
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    decided_at: datetime = Field(default_factory=_now)

    @property
    def is_complete(self) -> bool:
        """True when this result never needs recomputing on a resume."""
        return self.outcome in {"ok", "skipped"}


class ModelStats(BaseModel):
    """Per-model evidence for ordering pools (FR-6, and FR-14 later)."""

    model_config = ConfigDict(extra="forbid")

    answers: int = 0
    rejects: int = 0
    transient_failures: int = 0
    permanent_failures: int = 0
    calls: int = 0
    latency_ms_total: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def success_rate(self) -> float:
        """Share of answers that passed validation."""
        judged = self.answers + self.rejects
        return self.answers / judged if judged else 0.0

    @property
    def average_latency_ms(self) -> float:
        """Mean latency across completed calls."""
        return self.latency_ms_total / self.calls if self.calls else 0.0


class Report(BaseModel):
    """The persisted account of a run — re-printable without re-running."""

    model_config = ConfigDict(extra="forbid")

    remuda_version: str
    job_name: str
    run_id: str
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    rows: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    resumed_results: int = 0
    models: dict[str, ModelStats] = Field(default_factory=dict)
    distribution: dict[str, dict[str, int]] = Field(default_factory=dict)
    failures: list[LedgerEntry] = Field(default_factory=list)
    skips: list[LedgerEntry] = Field(default_factory=list)

    @property
    def is_successful(self) -> bool:
        """A run with any failed key never reports success (FR-4)."""
        return self.failed == 0

    @property
    def total_cost_usd(self) -> float:
        """Everything the run spent, as far as providers reported it."""
        return sum(stats.cost_usd for stats in self.models.values())

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> Self:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        return self
