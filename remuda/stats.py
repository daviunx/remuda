"""Cross-run statistics over persisted run ledgers (FR-14).

Read-only, and deliberately not clever: these numbers are the evidence an
operator uses to order a pool. remuda never reorders one itself — a tool that
quietly rewrote its own configuration from yesterday's latency would be
impossible to reason about.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime
from pathlib import Path

from remuda.ledger.models import ModelStats
from remuda.ledger.store import LedgerError, RunStore, list_runs


@dataclass(frozen=True)
class RunSummary:
    """One finished run, as its report recorded it."""

    job_name: str
    run_id: str
    path: Path
    started_at: datetime
    rows: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    models: dict[str, ModelStats] = dataclass_field(default_factory=dict)

    @property
    def is_successful(self) -> bool:
        """True when the run left no failed key behind."""
        return self.failed == 0

    @property
    def cost_usd(self) -> float:
        """Everything this run spent, as far as providers reported it."""
        return sum(stats.cost_usd for stats in self.models.values())


@dataclass
class ModelAggregate:
    """One model's record across every run that used it."""

    name: str
    answers: int = 0
    rejects: int = 0
    transient_failures: int = 0
    permanent_failures: int = 0
    calls: int = 0
    latency_ms_total: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    runs: int = 0
    last_seen: datetime | None = None

    @property
    def success_rate(self) -> float:
        """Share of this model's answers that passed validation."""
        judged = self.answers + self.rejects
        return self.answers / judged if judged else 0.0

    @property
    def rejection_rate(self) -> float:
        """Share of this model's answers that failed validation."""
        judged = self.answers + self.rejects
        return self.rejects / judged if judged else 0.0

    @property
    def average_latency_ms(self) -> float:
        """Mean latency across every call, weighted by call count."""
        return self.latency_ms_total / self.calls if self.calls else 0.0

    def absorb(self, stats: ModelStats, seen_at: datetime) -> None:
        """Fold one run's numbers for this model into the aggregate."""
        self.answers += stats.answers
        self.rejects += stats.rejects
        self.transient_failures += stats.transient_failures
        self.permanent_failures += stats.permanent_failures
        self.calls += stats.calls
        self.latency_ms_total += stats.latency_ms_total
        self.prompt_tokens += stats.prompt_tokens
        self.completion_tokens += stats.completion_tokens
        self.cost_usd += stats.cost_usd
        self.runs += 1
        if self.last_seen is None or seen_at > self.last_seen:
            self.last_seen = seen_at


def collect_runs(runs_root: Path, job_name: str | None = None) -> list[RunSummary]:
    """Every finished run under `runs_root`, newest first.

    A run directory without a report — a run that was killed before it
    finished — is skipped rather than fatal: stats must survive them.
    """
    summaries: list[RunSummary] = []
    for run_dir in list_runs(Path(runs_root), job_name):
        try:
            report = RunStore.open(run_dir).read_report()
        except LedgerError:
            continue
        summaries.append(
            RunSummary(
                job_name=report.job_name,
                run_id=report.run_id or run_dir.name,
                path=run_dir,
                started_at=report.started_at,
                rows=report.rows,
                ok=report.ok,
                skipped=report.skipped,
                failed=report.failed,
                models=dict(report.models),
            )
        )
    return summaries


def aggregate_models(summaries: Iterable[RunSummary]) -> list[ModelAggregate]:
    """Fold every run's per-model numbers together, best success rate first."""
    aggregates: dict[str, ModelAggregate] = {}
    for summary in summaries:
        for name, stats in summary.models.items():
            aggregate = aggregates.setdefault(name, ModelAggregate(name=name))
            aggregate.absorb(stats, summary.started_at)
    return sorted(
        aggregates.values(),
        key=lambda model: (model.success_rate, model.answers),
        reverse=True,
    )


def prunable_runs(summaries: Sequence[RunSummary], keep: int) -> list[RunSummary]:
    """The runs that fall outside the newest `keep` OF EACH JOB.

    Raises:
        ValueError: `keep` is not at least one — pruning every record of
            every run is not a retention policy.
    """
    if keep < 1:
        raise ValueError("keep must be at least one run per job")
    seen: dict[str, int] = {}
    removable: list[RunSummary] = []
    for summary in sorted(summaries, key=lambda run: run.run_id, reverse=True):
        count = seen.get(summary.job_name, 0) + 1
        seen[summary.job_name] = count
        if count > keep:
            removable.append(summary)
    return removable
