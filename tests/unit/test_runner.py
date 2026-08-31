"""FR-2/FR-4/FR-6/FR-7 — the runner: ordering, skips, reporting, sinks."""

import threading
from typing import TYPE_CHECKING, Any

import pytest

from remuda.api import run
from remuda.engine.lanes import ModelLane
from remuda.engine.plan import EngineError, select_fields
from remuda.engine.runner import PoolLanes, RowResult
from remuda.ledger.models import LedgerEntry
from remuda.registry.models import Pool, PoolEntry
from remuda.registry.registry import Registry
from remuda.spec.models import Job
from tests.fakes.transport import ScriptedTransport, model, provider

if TYPE_CHECKING:
    from remuda.engine.progress import ProgressEvent

ROWS = [
    {"id": "1", "title": "Leak", "brief": "A long enough brief to pass the guard"},
    {"id": "2", "title": "Noise", "brief": "short"},
]


def job_of(*fields: dict[str, Any], **overrides: Any) -> Job:
    return Job.model_validate(
        {
            "name": "runs",
            "input": {"path": "in.csv", "key": "id"},
            "fields": list(fields),
            **overrides,
        }
    )


def classify(name: str = "severity", **overrides: Any) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "classify",
        "pool": "cheap",
        "vocabulary": ["high", "low"],
        "prompt": {"inputs": ["title"], "template": "{{ title }}"},
        **overrides,
    }


def registry_of(*model_names: str, mopup: str | None = None) -> Registry:
    names = [*model_names, *([mopup] if mopup else [])]
    return Registry(
        providers=[provider()],
        models=[model(name) for name in names],
        pools=[
            Pool(
                name="cheap",
                entries=tuple(PoolEntry(model=name) for name in model_names),
                mopup=mopup,
                strategy="waterfall",
            )
        ],
    )


async def run_with(
    job: Job,
    transport: ScriptedTransport,
    registry: Registry | None = None,
    rows: list[dict[str, Any]] | None = None,
    **options: Any,
) -> Any:
    return await run(
        job,
        rows if rows is not None else ROWS,
        registry or registry_of("a"),
        transport_factory=lambda _provider: transport,
        run_id="test-run",
        **options,
    )


class TestOutcomes:
    async def test_every_row_is_derived_and_counted(self) -> None:
        transport = ScriptedTransport(default="high")

        report = await run_with(job_of(classify()), transport)

        assert report.ok == 2
        assert report.failed == 0
        assert report.is_successful

    async def test_a_failed_key_makes_the_run_unsuccessful(self) -> None:
        transport = ScriptedTransport({"a": ["high", "not-a-label", "still-not"]})

        report = await run_with(job_of(classify()), transport)

        assert report.ok == 1
        assert report.failed == 1
        assert report.is_successful is False
        assert report.failures[0].reason is not None

    async def test_distribution_counts_each_label(self) -> None:
        transport = ScriptedTransport({"a": ["high", "low"]})

        report = await run_with(job_of(classify()), transport)

        assert report.distribution["severity"] == {"high": 1, "low": 1}

    async def test_model_statistics_are_reported(self) -> None:
        transport = ScriptedTransport({"a": ["nope", "high", "low"]})

        report = await run_with(job_of(classify()), transport)

        assert report.models["a"].answers == 2
        assert report.models["a"].rejects == 1
        assert report.models["a"].calls == 3

    async def test_transports_are_closed(self) -> None:
        transport = ScriptedTransport(default="high")

        await run_with(job_of(classify()), transport)

        assert transport.closed is True


class TestPreconditionsAndDependencies:
    async def test_precondition_skips_are_counted_not_failed(self) -> None:
        transport = ScriptedTransport(default="high")
        job = job_of(
            classify(
                "hook",
                when={"source": "brief", "min_length": 20},
                prompt={"inputs": ["brief"], "template": "{{ brief }}"},
            )
        )

        report = await run_with(job, transport)

        assert report.ok == 1
        assert report.skipped == 1
        assert report.failed == 0
        assert report.is_successful
        assert "brief" in (report.skips[0].reason or "")

    async def test_a_skipped_row_costs_no_model_call(self) -> None:
        transport = ScriptedTransport(default="high")
        job = job_of(
            classify(
                "hook",
                when={"source": "brief", "min_length": 20},
                prompt={"inputs": ["brief"], "template": "{{ brief }}"},
            )
        )

        await run_with(job, transport)

        assert len(transport.requests) == 1

    async def test_dependency_is_derived_before_its_dependent(self) -> None:
        transport = ScriptedTransport(default="high")
        job = job_of(classify("hook", depends_on="severity"), classify("severity"))

        assert [f.name for f in select_fields(job)] == ["severity", "hook"]

        report = await run_with(job, transport, rows=[ROWS[0]])
        assert report.ok == 2

    async def test_a_dependent_field_is_skipped_when_its_dependency_failed(
        self,
    ) -> None:
        transport = ScriptedTransport({"a": ["not-a-label", "not-a-label"]})
        job = job_of(classify("severity"), classify("hook", depends_on="severity"))

        report = await run_with(job, transport, rows=[ROWS[0]])

        assert report.failed == 1
        assert report.skipped == 1
        assert "severity" in (report.skips[0].reason or "")

    async def test_selecting_a_dependent_without_its_dependency_is_refused(
        self,
    ) -> None:
        job = job_of(classify("severity"), classify("hook", depends_on="severity"))

        with pytest.raises(EngineError, match="depends on"):
            select_fields(job, ["hook"])

    async def test_unknown_field_selection_is_refused(self) -> None:
        with pytest.raises(EngineError, match="nope"):
            select_fields(job_of(classify()), ["nope"])


class TestMapFields:
    async def test_map_field_is_resolved_without_any_model_call(self) -> None:
        transport = ScriptedTransport(default="high")
        job = job_of(
            {
                "name": "city",
                "kind": "map",
                "map": {"lookup": "title", "table": {"Leak": "plumbing"}},
            }
        )

        report = await run_with(job, transport, rows=[ROWS[0]])

        assert report.ok == 1
        assert transport.requests == []

    async def test_unmapped_value_fails_the_key(self) -> None:
        transport = ScriptedTransport(default="high")
        job = job_of(
            {
                "name": "city",
                "kind": "map",
                "map": {"lookup": "title", "table": {"Leak": "plumbing"}},
            }
        )

        report = await run_with(job, transport, rows=[ROWS[1]])

        assert report.failed == 1


class TestSinkAndProgress:
    async def test_sink_receives_each_row_once_all_its_fields_are_decided(
        self,
    ) -> None:
        transport = ScriptedTransport(default="high")
        received: list[RowResult] = []
        job = job_of(classify("severity"), classify("tone"))

        await run_with(job, transport, sink=received.append)

        assert [result.key for result in received] == ["1", "2"]
        assert received[0].value("severity") == "high"
        assert set(received[0].fields) == {"severity", "tone"}

    async def test_sink_carries_the_untouched_row(self) -> None:
        transport = ScriptedTransport(default="high")
        received: list[RowResult] = []

        await run_with(job_of(classify()), transport, sink=received.append)

        assert received[0].row["title"] == "Leak"

    async def test_progress_is_emitted_per_chunk(self) -> None:
        transport = ScriptedTransport(default="high")
        events: list[ProgressEvent] = []

        await run_with(job_of(classify()), transport, progress=events.append)

        assert [event.chunk for event in events] == [1, 2]
        assert events[0].chunks == 2
        assert events[0].field == "severity"
        assert events[0].models == ("a",)
        assert "chunk 1/2" in events[0].summary()

    async def test_packing_reduces_the_number_of_chunks(self) -> None:
        transport = ScriptedTransport({"a": ["1: high\n2: low"]})
        events: list[ProgressEvent] = []
        job = job_of(classify(), execution={"records_per_call": 2})

        report = await run_with(job, transport, progress=events.append)

        assert len(transport.requests) == 1
        assert [event.chunks for event in events] == [1]
        assert report.ok == 2


class TestPoolBehaviour:
    async def test_mop_up_is_always_last_even_when_scattering(self) -> None:
        transport = ScriptedTransport()
        lanes = PoolLanes(
            members=(
                ModelLane(model=model("a"), transport=transport),
                ModelLane(model=model("b"), transport=transport),
            ),
            mopup=ModelLane(model=model("paid"), transport=transport),
            strategy="scatter",
        )

        assert [lane.name for lane in lanes.ladder_for(0)] == ["a", "b", "paid"]
        assert [lane.name for lane in lanes.ladder_for(1)] == ["b", "a", "paid"]

    async def test_waterfall_starts_every_chunk_at_the_first_model(self) -> None:
        transport = ScriptedTransport(default="high")
        registry = registry_of("a", "b")

        report = await run_with(job_of(classify()), transport, registry)

        assert report.models["a"].answers == 2
        assert report.models["b"].calls == 0

    async def test_pool_override_replaces_the_declared_pool(self) -> None:
        transport = ScriptedTransport(default="high")
        registry = Registry(
            providers=[provider()],
            models=[model("z")],
            pools=[
                Pool(name="cheap", entries=(PoolEntry(model="z"),)),
                Pool(name="other", entries=(PoolEntry(model="z"),)),
            ],
        )

        report = await run_with(job_of(classify()), transport, registry, pool="other")

        assert report.ok == 2

    async def test_a_discovery_pool_is_refused_with_an_explanation(self) -> None:
        registry = Registry(
            providers=[provider()],
            pools=[
                Pool(
                    name="cheap",
                    entries=(
                        PoolEntry(
                            discover={"provider": "fake", "free": True},
                        ),
                    ),
                )
            ],
        )

        with pytest.raises(EngineError, match="catalog"):
            await run_with(job_of(classify()), ScriptedTransport(), registry)


class TestResume:
    async def test_already_decided_results_are_not_recomputed(self) -> None:
        transport = ScriptedTransport(default="high")
        done = {
            ("1", "severity"): LedgerEntry(
                key="1", field="severity", outcome="ok", value="low", model="a"
            )
        }

        report = await run_with(job_of(classify()), transport, done=done)

        assert report.resumed_results == 1
        assert report.ok == 2
        assert len(transport.requests) == 1

    async def test_a_resumed_value_still_feeds_a_dependent_field(self) -> None:
        transport = ScriptedTransport(default="high")
        job = job_of(
            classify("severity"),
            classify(
                "hook",
                depends_on="severity",
                when={"source": "severity", "one_of": ["low"]},
            ),
        )
        done = {
            ("1", "severity"): LedgerEntry(
                key="1", field="severity", outcome="ok", value="low", model="a"
            )
        }

        report = await run_with(job, transport, rows=[ROWS[0]], done=done)

        assert report.ok == 2
        assert report.skipped == 0


class TestRowIntegrity:
    async def test_duplicate_keys_are_refused(self) -> None:
        rows = [{"id": "1", "title": "a"}, {"id": "1", "title": "b"}]

        with pytest.raises(EngineError, match="duplicate key"):
            await run_with(job_of(classify()), ScriptedTransport(), rows=rows)

    async def test_a_row_missing_a_declared_prompt_input_fails_that_key(self) -> None:
        transport = ScriptedTransport(default="high")
        rows = [{"id": "1"}, {"id": "2", "title": "Noise"}]

        report = await run_with(job_of(classify()), transport, rows=rows)

        assert report.failed == 1
        assert report.ok == 1
        assert "title" in (report.failures[0].reason or "")


class TestLedgerWriting:
    async def test_the_ledger_write_never_runs_on_the_event_loop(self) -> None:
        """The append opens, writes and fsyncs — blocking work the loop must
        not do, or every sibling chunk's request waits behind it."""
        threads: list[str] = []

        def write(_entry: LedgerEntry) -> None:
            threads.append(threading.current_thread().name)

        transport = ScriptedTransport(default="high")
        report = await run_with(job_of(classify()), transport, write=write)

        assert report.ok == 2
        assert len(threads) == 2
        assert threading.main_thread().name not in threads

    async def test_every_decided_result_is_written_exactly_once(self) -> None:
        written: list[tuple[str, str]] = []
        transport = ScriptedTransport(default="high")

        report = await run_with(
            job_of(classify()),
            transport,
            write=lambda entry: written.append((entry.key, entry.outcome)),
        )

        assert report.ok == 2
        assert sorted(written) == [("1", "ok"), ("2", "ok")]
