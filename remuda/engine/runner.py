"""The run orchestrator: chunks, workers, and the account of what happened.

Field by field, in dependency order: decide what is eligible, pack the rest
into chunks, walk each chunk down the ladder with bounded concurrency, and
record every decided (row, field) exactly once.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import Any

from remuda.catalog.resolve import ResolvedPool, member_to_model
from remuda.engine.ladder import ChunkItem, ChunkResult, KeyOutcome, run_chunk
from remuda.engine.lanes import ModelLane
from remuda.engine.packing import chunk_keys
from remuda.engine.plan import EngineError, Skip, eligibility, select_fields
from remuda.engine.progress import ProgressCallback, ProgressEvent
from remuda.ledger.models import LedgerEntry, Report
from remuda.registry.models import ModelConfig, Provider
from remuda.registry.registry import Registry
from remuda.rows import row_key
from remuda.spec.errors import PromptRenderError
from remuda.spec.models import FieldSpec, Job
from remuda.transport.models import Transport
from remuda.transport.openai_compat import build_request_extras, build_transport
from remuda.validate.answers import evaluate_map

#: Values worth counting a distribution for — a free-text field has none.
_COUNTED_KINDS = frozenset({"classify", "map", "score"})

LedgerWriter = Callable[[LedgerEntry], None]
TransportFactory = Callable[[Provider], Transport]


@dataclass(frozen=True)
class PoolLanes:
    """A pool's lanes: its ordered members, and the mop-up that follows them."""

    members: tuple[ModelLane, ...]
    mopup: ModelLane | None = None
    strategy: str = "scatter"

    def ladder_for(self, chunk_index: int) -> list[ModelLane]:
        """The lane order this chunk walks. Mop-up is always last."""
        members = list(self.members)
        if self.strategy == "scatter" and members:
            start = chunk_index % len(members)
            members = [*members[start:], *members[:start]]
        return [*members, *([self.mopup] if self.mopup is not None else [])]

    @property
    def every_lane(self) -> tuple[ModelLane, ...]:
        """Members plus mop-up, for statistics."""
        return (*self.members, *((self.mopup,) if self.mopup is not None else ()))


@dataclass(frozen=True)
class RowResult:
    """Every field decided for one row — handed to the sink (FR-7)."""

    key: str
    row: Mapping[str, Any]
    fields: Mapping[str, LedgerEntry] = dataclass_field(default_factory=dict)

    def value(self, field: str) -> Any:
        """The derived value, or None when the field was skipped or failed."""
        entry = self.fields.get(field)
        return entry.value if entry is not None else None


Sink = Callable[[RowResult], None]


class Runner:
    """Executes one job over a set of rows."""

    def __init__(
        self,
        job: Job,
        registry: Registry,
        *,
        run_id: str,
        version: str,
        pool: str | None = None,
        only: Sequence[str] | None = None,
        transport_factory: TransportFactory = build_transport,
        progress: ProgressCallback | None = None,
        sink: Sink | None = None,
        write: LedgerWriter | None = None,
        done: Mapping[tuple[str, str], LedgerEntry] | None = None,
        resolved_pools: Mapping[str, ResolvedPool] | None = None,
    ) -> None:
        self._job = job
        self._registry = registry
        self._pool_override = pool
        self._fields = select_fields(job, only)
        self._transport_factory = transport_factory
        self._progress = progress
        self._sink = sink
        self._write = write
        self._done = dict(done or {})
        self._resolved = dict(resolved_pools or {})
        self._transports: dict[str, Transport] = {}
        self._lanes: dict[str, PoolLanes] = {}
        self._derived: dict[str, dict[str, Any]] = {}
        self._pending_fields: dict[str, int] = {}
        self._collected: dict[str, dict[str, LedgerEntry]] = {}
        self._rows: dict[str, Mapping[str, Any]] = {}
        self._report = Report(remuda_version=version, job_name=job.name, run_id=run_id)

    async def run(self, rows: Sequence[Mapping[str, Any]]) -> Report:
        """Derive every selected field for every row, and report on it.

        Raises:
            EngineError: the run cannot be planned (unknown pool, a pool that
                needs catalog discovery, an unreadable row key).
        """
        self._seed(rows)
        try:
            for field in self._fields:
                await self._run_field(field)
        finally:
            self._stats_into_report()
            await self._close_transports()
        self._report.finished_at = datetime.now(UTC)
        return self._report

    # -- setup -------------------------------------------------------------

    def _seed(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for row in rows:
            key = row_key(row, self._job.input.key)
            if key in self._rows:
                raise EngineError(
                    f"duplicate key '{key}' in column '{self._job.input.key}' — "
                    "every row needs a unique key"
                )
            self._rows[key] = row
            self._derived[key] = {}
            self._collected[key] = {}
            self._pending_fields[key] = len(self._fields)
        self._report.rows = len(self._rows)
        self._seed_resumed()

    def _seed_resumed(self) -> None:
        selected = {field.name for field in self._fields}
        for (key, name), entry in self._done.items():
            if key not in self._rows or name not in selected:
                continue
            self._report.resumed_results += 1
            self._record(entry, counted=True)

    def _lanes_for(self, field: FieldSpec) -> PoolLanes:
        pool_name = self._pool_name(field)
        if pool_name in self._lanes:
            return self._lanes[pool_name]

        lanes = self._lanes_from_resolution(pool_name) or self._lanes_from_registry(
            pool_name
        )
        self._lanes[pool_name] = lanes
        return lanes

    def _lanes_from_resolution(self, pool_name: str) -> PoolLanes | None:
        """Lanes from the pool snapshot taken when the run started (FR-3)."""
        resolved = self._resolved.get(pool_name)
        if resolved is None:
            return None
        if not resolved.members:
            raise EngineError(
                f"pool '{pool_name}' resolved to no models — nothing to run"
            )
        return PoolLanes(
            members=tuple(
                self._lane_for(member_to_model(member)) for member in resolved.members
            ),
            mopup=(
                self._lane_for(member_to_model(resolved.mopup))
                if resolved.mopup is not None
                else None
            ),
            strategy=resolved.strategy,
        )

    def _lanes_from_registry(self, pool_name: str) -> PoolLanes:
        pool = self._registry.pool(pool_name)
        if pool.discover_queries:
            raise EngineError(
                f"pool '{pool.name}' resolves its members from a provider "
                "catalog, which was not resolved for this run — run it through "
                "`remuda run`, which resolves and snapshots pools"
            )
        if not pool.model_names:
            raise EngineError(f"pool '{pool.name}' has no models to run")
        return PoolLanes(
            members=tuple(self._build_lane(name) for name in pool.model_names),
            mopup=self._build_lane(pool.mopup) if pool.mopup else None,
            strategy=pool.strategy,
        )

    def _pool_name(self, field: FieldSpec) -> str:
        pool_name = self._pool_override or field.pool
        if pool_name is None:  # unreachable: only map fields have no pool
            raise EngineError(f"field '{field.name}' names no pool")
        return pool_name

    def _build_lane(self, model_name: str) -> ModelLane:
        return self._lane_for(self._registry.model(model_name))

    def _lane_for(self, model: ModelConfig) -> ModelLane:
        provider = self._registry.provider(model.provider)
        transport = self._transports.get(provider.name)
        if transport is None:
            transport = self._transport_factory(provider)
            self._transports[provider.name] = transport
        return ModelLane(
            model=model,
            transport=transport,
            extra_body=build_request_extras(model),
            timeout_seconds=provider.timeout_seconds,
        )

    async def _close_transports(self) -> None:
        for transport in self._transports.values():
            await transport.aclose()
        self._transports.clear()

    # -- per-field execution -----------------------------------------------

    async def _run_field(self, field: FieldSpec) -> None:
        items = self._eligible_items(field)
        if not items:
            return
        if field.kind == "map":
            self._resolve_map(field, items)
            return

        by_key = {item.key: item for item in items}
        groups = chunk_keys(list(by_key), self._job.execution.records_per_call)
        lanes = self._lanes_for(field)
        semaphore = asyncio.Semaphore(self._job.execution.workers)

        async def run_one(index: int, keys: tuple[str, ...]) -> None:
            async with semaphore:
                result = await run_chunk(
                    field=field,
                    items=[by_key[key] for key in keys],
                    lanes=lanes.ladder_for(index),
                    attempts_per_model=self._job.execution.attempts_per_model,
                )
            self._absorb(field, index, len(groups), result)

        async with asyncio.TaskGroup() as group:
            for index, keys in enumerate(groups):
                group.create_task(run_one(index, keys))

    def _eligible_items(self, field: FieldSpec) -> list[ChunkItem]:
        items: list[ChunkItem] = []
        for key, row in self._rows.items():
            if (key, field.name) in self._done:
                continue
            skip = eligibility(field, key, row, self._derived[key])
            if skip is not None:
                self._record_skip(skip)
                continue
            if field.kind == "map":
                items.append(ChunkItem(key=key, row=row, prompt=""))
                continue
            try:
                prompt = field.render(row)
            except PromptRenderError as error:
                self._record_failure(key, field.name, str(error))
                continue
            items.append(ChunkItem(key=key, row=row, prompt=prompt))
        return items

    def _resolve_map(self, field: FieldSpec, items: Sequence[ChunkItem]) -> None:
        for index, item in enumerate(items):
            verdict = evaluate_map(field, item.row)
            entry = LedgerEntry(
                key=item.key,
                field=field.name,
                outcome="ok" if verdict.is_valid else "failed",
                value=verdict.value,
                reason=None if verdict.is_valid else verdict.repair,
            )
            self._record(entry)
            self._emit(field, index, len(items), entry)

    def _absorb(
        self, field: FieldSpec, index: int, chunks: int, result: ChunkResult
    ) -> None:
        entries = [self._entry_for(field, outcome) for outcome in result.outcomes]
        for entry in entries:
            self._record(entry)
        self._emit_chunk(field, index, chunks, entries, result)

    def _entry_for(self, field: FieldSpec, outcome: KeyOutcome) -> LedgerEntry:
        return LedgerEntry(
            key=outcome.key,
            field=field.name,
            outcome=outcome.outcome,
            value=outcome.value,
            model=outcome.model,
            attempts=outcome.attempts,
            rejects=outcome.rejects,
            reason=outcome.reason,
            latency_ms=outcome.latency_ms,
            prompt_tokens=outcome.prompt_tokens,
            completion_tokens=outcome.completion_tokens,
            cost_usd=outcome.cost_usd,
        )

    # -- recording ---------------------------------------------------------

    def _record_skip(self, skip: Skip) -> None:
        self._record(
            LedgerEntry(
                key=skip.key, field=skip.field, outcome="skipped", reason=skip.reason
            )
        )

    def _record_failure(self, key: str, field: str, reason: str) -> None:
        self._record(LedgerEntry(key=key, field=field, outcome="failed", reason=reason))

    def _record(self, entry: LedgerEntry, counted: bool = False) -> None:
        if entry.outcome == "ok":
            self._report.ok += 1
            self._derived[entry.key][entry.field] = entry.value
            self._count_distribution(entry)
        elif entry.outcome == "skipped":
            self._report.skipped += 1
            self._report.skips.append(entry)
        else:
            self._report.failed += 1
            self._report.failures.append(entry)

        self._collected[entry.key][entry.field] = entry
        if not counted and self._write is not None:
            self._write(entry)
        self._settle_row(entry.key)

    def _count_distribution(self, entry: LedgerEntry) -> None:
        field = self._job.field(entry.field)
        if field.kind not in _COUNTED_KINDS:
            return
        bucket = self._report.distribution.setdefault(entry.field, {})
        label = str(entry.value)
        bucket[label] = bucket.get(label, 0) + 1

    def _settle_row(self, key: str) -> None:
        self._pending_fields[key] -= 1
        if self._pending_fields[key] > 0 or self._sink is None:
            return
        self._sink(
            RowResult(key=key, row=self._rows[key], fields=dict(self._collected[key]))
        )

    # -- progress ----------------------------------------------------------

    def _emit(
        self, field: FieldSpec, index: int, chunks: int, entry: LedgerEntry
    ) -> None:
        self._emit_chunk(field, index, chunks, [entry], ChunkResult())

    def _emit_chunk(
        self,
        field: FieldSpec,
        index: int,
        chunks: int,
        entries: Sequence[LedgerEntry],
        result: ChunkResult,
    ) -> None:
        if self._progress is None:
            return
        counts = dict.fromkeys(("ok", "skipped", "failed"), 0)
        for entry in entries:
            counts[entry.outcome] += 1
        distribution = self._report.distribution.get(field.name, {})
        self._progress(
            ProgressEvent(
                chunk=index + 1,
                chunks=chunks,
                field=field.name,
                models=tuple(result.models_used),
                ok=counts["ok"],
                failed=counts["failed"],
                skipped=counts["skipped"],
                notes=tuple(result.notes),
                distribution=tuple(sorted(distribution.items())),
            )
        )

    def _stats_into_report(self) -> None:
        for lanes in self._lanes.values():
            for lane in lanes.every_lane:
                self._report.models[lane.name] = lane.stats
