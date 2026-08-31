"""FR-14 — cross-run statistics, read-only over persisted ledgers.

Deliberately NOT auto-tuning: these numbers inform the operator's ordering of
a pool; remuda never reorders one itself.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remuda.ledger.models import ModelStats, Report
from remuda.ledger.resume import build_lock
from remuda.ledger.store import RunStore
from remuda.spec.models import Job
from remuda.stats import aggregate_models, collect_runs, prunable_runs

JOB_SPEC = {
    "name": "counted",
    "input": {"path": "in.csv", "key": "id"},
    "fields": [
        {
            "name": "severity",
            "kind": "classify",
            "pool": "cheap",
            "vocabulary": ["high", "low"],
            "prompt": {"inputs": ["title"], "template": "{{ title }}"},
        }
    ],
}


@pytest.fixture
def job() -> Job:
    return Job.model_validate(JOB_SPEC)


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    path = tmp_path / "in.csv"
    path.write_text("id,title\n1,Leak\n", encoding="utf-8")
    return path


def write_run(
    runs_root: Path,
    job: Job,
    input_file: Path,
    run_id: str,
    *,
    ok: int = 0,
    failed: int = 0,
    skipped: int = 0,
    models: dict[str, ModelStats] | None = None,
    started_at: datetime | None = None,
) -> Path:
    store = RunStore.create(
        runs_root, build_lock(job, input_file, "0.1.0"), run_id=run_id
    )
    store.write_job(job)
    store.write_report(
        Report(
            remuda_version="0.1.0",
            job_name=job.name,
            run_id=run_id,
            started_at=started_at or datetime.now(UTC),
            finished_at=(started_at or datetime.now(UTC)) + timedelta(seconds=30),
            rows=ok + failed + skipped,
            ok=ok,
            failed=failed,
            skipped=skipped,
            models=models or {},
        )
    )
    return store.run_dir


def stats_of(**overrides: float) -> ModelStats:
    return ModelStats.model_validate({"calls": 1, **overrides})


class TestCollectRuns:
    def test_every_run_with_a_report_is_collected(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        write_run(tmp_path, job, input_file, "20260101T000000Z", ok=5)
        write_run(tmp_path, job, input_file, "20260102T000000Z", ok=3, failed=1)

        collected = collect_runs(tmp_path)

        assert [summary.run_id for summary in collected] == [
            "20260102T000000Z",
            "20260101T000000Z",
        ]
        assert collected[0].failed == 1
        assert collected[0].job_name == "counted"

    def test_a_run_without_a_report_is_skipped_not_fatal(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        """A killed run has a ledger but no report; stats must still work."""
        write_run(tmp_path, job, input_file, "20260101T000000Z", ok=1)
        RunStore.create(
            tmp_path, build_lock(job, input_file, "0.1.0"), run_id="20260103T000000Z"
        )

        collected = collect_runs(tmp_path)

        assert [summary.run_id for summary in collected] == ["20260101T000000Z"]

    def test_an_empty_runs_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert collect_runs(tmp_path / "absent") == []

    def test_runs_can_be_filtered_by_job(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        other = Job.model_validate({**JOB_SPEC, "name": "other"})
        write_run(tmp_path, job, input_file, "20260101T000000Z", ok=1)
        write_run(tmp_path, other, input_file, "20260102T000000Z", ok=1)

        assert len(collect_runs(tmp_path, job_name="other")) == 1


class TestAggregateModels:
    def test_counts_add_up_across_runs(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        write_run(
            tmp_path,
            job,
            input_file,
            "20260101T000000Z",
            ok=2,
            models={"a": stats_of(answers=2, rejects=1, calls=3, cost_usd=0.5)},
        )
        write_run(
            tmp_path,
            job,
            input_file,
            "20260102T000000Z",
            ok=1,
            models={"a": stats_of(answers=1, rejects=1, calls=2, cost_usd=0.25)},
        )

        aggregates = aggregate_models(collect_runs(tmp_path))

        assert len(aggregates) == 1
        model = aggregates[0]
        assert model.answers == 3
        assert model.rejects == 2
        assert model.calls == 5
        assert model.cost_usd == 0.75
        assert model.runs == 2

    def test_success_rate_is_answers_over_judged_answers(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        write_run(
            tmp_path,
            job,
            input_file,
            "20260101T000000Z",
            models={"a": stats_of(answers=3, rejects=1)},
        )

        assert aggregate_models(collect_runs(tmp_path))[0].success_rate == 0.75

    def test_models_are_ranked_by_success_rate(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        write_run(
            tmp_path,
            job,
            input_file,
            "20260101T000000Z",
            models={
                "weak": stats_of(answers=1, rejects=9),
                "strong": stats_of(answers=9, rejects=1),
            },
        )

        assert [model.name for model in aggregate_models(collect_runs(tmp_path))] == [
            "strong",
            "weak",
        ]

    def test_average_latency_is_weighted_by_calls(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        write_run(
            tmp_path,
            job,
            input_file,
            "20260101T000000Z",
            models={"a": stats_of(calls=2, latency_ms_total=200.0)},
        )
        write_run(
            tmp_path,
            job,
            input_file,
            "20260102T000000Z",
            models={"a": stats_of(calls=2, latency_ms_total=600.0)},
        )

        assert aggregate_models(collect_runs(tmp_path))[0].average_latency_ms == 200.0

    def test_last_seen_is_the_most_recent_run_that_used_the_model(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 6, 1, tzinfo=UTC)
        write_run(
            tmp_path,
            job,
            input_file,
            "20260101T000000Z",
            models={"a": stats_of(answers=1)},
            started_at=early,
        )
        write_run(
            tmp_path,
            job,
            input_file,
            "20260601T000000Z",
            models={"a": stats_of(answers=1), "b": stats_of(answers=1)},
            started_at=late,
        )

        by_name = {
            model.name: model for model in aggregate_models(collect_runs(tmp_path))
        }

        assert by_name["a"].last_seen == late
        assert by_name["b"].last_seen == late

    def test_counts_reconcile_with_the_individual_reports(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        """FR-14 acceptance: the aggregate must equal the sum of its runs."""
        write_run(
            tmp_path,
            job,
            input_file,
            "20260101T000000Z",
            models={"a": stats_of(answers=4, rejects=2)},
        )
        write_run(
            tmp_path,
            job,
            input_file,
            "20260102T000000Z",
            models={"a": stats_of(answers=6, rejects=3)},
        )
        runs = collect_runs(tmp_path)

        aggregate = aggregate_models(runs)[0]

        assert aggregate.answers == sum(summary.models["a"].answers for summary in runs)
        assert aggregate.rejects == sum(summary.models["a"].rejects for summary in runs)


class TestPrune:
    def test_the_newest_runs_per_job_are_kept(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        for run_id in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            write_run(tmp_path, job, input_file, run_id, ok=1)

        removable = prunable_runs(collect_runs(tmp_path), keep=1)

        assert [summary.run_id for summary in removable] == [
            "20260102T000000Z",
            "20260101T000000Z",
        ]

    def test_each_job_keeps_its_own_newest(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        other = Job.model_validate({**JOB_SPEC, "name": "other"})
        write_run(tmp_path, job, input_file, "20260101T000000Z", ok=1)
        write_run(tmp_path, job, input_file, "20260102T000000Z", ok=1)
        write_run(tmp_path, other, input_file, "20260103T000000Z", ok=1)

        removable = prunable_runs(collect_runs(tmp_path), keep=1)

        assert [summary.run_id for summary in removable] == ["20260101T000000Z"]

    def test_keeping_more_than_exist_removes_nothing(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        write_run(tmp_path, job, input_file, "20260101T000000Z", ok=1)

        assert prunable_runs(collect_runs(tmp_path), keep=5) == []

    def test_keeping_none_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            prunable_runs([], keep=0)
