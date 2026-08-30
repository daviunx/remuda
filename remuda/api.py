"""The embedding surface (FR-7).

A host application runs the same jobs remuda's CLI runs: rows from any
source, results delivered per completed row, the same ladder and the same
report object. No files are required — pass rows in, get a Report back.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from remuda.catalog.resolve import ResolvedPool, resolve_pool
from remuda.engine.progress import ProgressCallback
from remuda.engine.runner import LedgerWriter, Runner, Sink, TransportFactory
from remuda.ledger.models import LedgerEntry, Report
from remuda.ledger.resume import OpenedRun, build_lock, open_run
from remuda.ledger.store import RUNS_DIRNAME, RunStore, new_run_id
from remuda.preflight import preflight_opencode
from remuda.registry.registry import Registry
from remuda.rows import read_rows
from remuda.spec.models import Job
from remuda.transport.openai_compat import build_transport
from remuda.version import __version__


@dataclass(frozen=True)
class JobRunOutcome:
    """A file-backed run: what happened, and where it was recorded."""

    report: Report
    store: RunStore
    is_resumed: bool
    announcements: tuple[str, ...] = ()


async def run(
    job: Job,
    rows: Sequence[Mapping[str, Any]],
    registry: Registry,
    *,
    pool: str | None = None,
    only: Sequence[str] | None = None,
    sink: Sink | None = None,
    progress: ProgressCallback | None = None,
    write: LedgerWriter | None = None,
    done: Mapping[tuple[str, str], LedgerEntry] | None = None,
    run_id: str | None = None,
    resolved_pools: Mapping[str, ResolvedPool] | None = None,
    transport_factory: TransportFactory = build_transport,
) -> Report:
    """Derive every selected field for every row.

    Args:
        job: the job definition — from a directory or built in code.
        rows: plain mappings; every column rides through untouched.
        registry: providers, models and pools by name.
        pool: overrides the pool every field names.
        only: field selection; dependencies must be included.
        sink: called once per row, when all its fields are decided.
        progress: called once per chunk (FR-6).
        write: called for every decided result — the ledger's append.
        done: results a resume must not recompute.
        run_id: identifies the run in the report.
        resolved_pools: pools already materialized for this run (FR-3).
        transport_factory: how a provider becomes a transport.

    Raises:
        EngineError: the run cannot be planned.
    """
    runner = Runner(
        job=job,
        registry=registry,
        run_id=run_id or new_run_id(),
        version=__version__,
        pool=pool,
        only=only,
        transport_factory=transport_factory,
        progress=progress,
        sink=sink,
        write=write,
        done=done,
        resolved_pools=resolved_pools,
    )
    return await runner.run(rows)


def run_sync(
    job: Job,
    rows: Sequence[Mapping[str, Any]],
    registry: Registry,
    **options: Any,
) -> Report:
    """Blocking wrapper around `run()` for callers with no event loop."""
    return asyncio.run(run(job, rows, registry, **options))


async def run_job_dir(
    job_dir: Path,
    registry: Registry,
    *,
    runs_root: Path | None = None,
    fresh: bool = False,
    limit: int | None = None,
    pool: str | None = None,
    only: Sequence[str] | None = None,
    fill_missing: str | None = None,
    sink: Sink | None = None,
    progress: ProgressCallback | None = None,
    transport_factory: TransportFactory = build_transport,
) -> JobRunOutcome:
    """Run a job directory, recording everything in a run directory.

    Raises:
        SpecValidationError: the job directory is misdeclared.
        ResumeRefusedError: the previous run used a different job or input.
        EngineError: the run cannot be planned.
    """
    job = Job.from_dir(job_dir)
    input_path = Path(job_dir) / job.input.path
    rows = read_rows(input_path, job.input.format, limit=limit)
    if fill_missing:
        rows = [row for row in rows if not str(row.get(fill_missing, "")).strip()]

    opened = open_run(
        runs_root=runs_root or Path(job_dir) / RUNS_DIRNAME,
        lock=build_lock(job, input_path, __version__),
        fresh=fresh,
    )
    opened.store.write_job(job)
    resolved = await _resolved_pools(job, registry, opened, pool)
    announcements = await preflight_opencode(registry, resolved.values())
    report = await _run_into(
        opened,
        job=job,
        rows=rows,
        registry=registry,
        resolved_pools=resolved,
        pool=pool,
        only=only,
        sink=sink,
        progress=progress,
        transport_factory=transport_factory,
    )
    return JobRunOutcome(
        report=report,
        store=opened.store,
        is_resumed=opened.is_resumed,
        announcements=tuple(announcements),
    )


async def _run_into(opened: OpenedRun, **options: Any) -> Report:
    """Run into an opened run directory, always closing and reporting."""
    store = opened.store
    try:
        report = await run(
            run_id=store.run_id,
            write=store.append,
            done=opened.done,
            **options,
        )
    finally:
        store.close()
    store.write_report(report)
    return report


async def _resolved_pools(
    job: Job,
    registry: Registry,
    opened: OpenedRun,
    pool_override: str | None,
) -> dict[str, ResolvedPool]:
    """Materialize every pool the run needs, reusing a resume's snapshot.

    A resumed run must keep the models it started with: free-tier membership
    churns, and re-resolving would silently change who answered the run.
    """
    snapshot = opened.store.read_resolved_pools()
    wanted = {
        pool_override or field.pool
        for field in job.fields
        if (pool_override or field.pool) is not None
    }
    resolved: dict[str, ResolvedPool] = {}
    pending = [name for name in wanted if name is not None and name not in snapshot]
    if pending:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for name in sorted(pending):
                resolved[name] = await resolve_pool(
                    registry.pool(name), registry, client
                )
    resolved.update(snapshot)
    if resolved:
        opened.store.write_resolved_pools(list(resolved.values()))
    return resolved
