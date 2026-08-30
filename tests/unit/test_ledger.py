"""FR-5/FR-6 — the run directory: crash-safe ledger, lock, resume refusal."""

from pathlib import Path
from typing import Any

import pytest

from remuda.ledger.models import LedgerEntry, Report, SpecLock
from remuda.ledger.resume import ResumeRefusedError, build_lock, open_run
from remuda.ledger.store import (
    LEDGER_FILENAME,
    LedgerError,
    RunStore,
    file_fingerprint,
    job_fingerprint,
    list_runs,
    new_run_id,
)
from remuda.spec.models import Job

JOB_SPEC: dict[str, Any] = {
    "name": "ledgered",
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

INPUT_CSV = "id,title\n1,Leak\n2,Noise\n"


@pytest.fixture
def job() -> Job:
    return Job.model_validate(JOB_SPEC)


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    path = tmp_path / "in.csv"
    path.write_text(INPUT_CSV, encoding="utf-8")
    return path


def entry(key: str, outcome: str = "ok", **overrides: Any) -> LedgerEntry:
    return LedgerEntry.model_validate(
        {
            "key": key,
            "field": "severity",
            "outcome": outcome,
            "value": "high" if outcome == "ok" else None,
            **overrides,
        }
    )


class TestFingerprints:
    def test_job_fingerprint_is_stable(self, job: Job) -> None:
        assert job_fingerprint(job) == job_fingerprint(Job.model_validate(JOB_SPEC))

    def test_job_fingerprint_moves_with_the_definition(self, job: Job) -> None:
        changed = Job.model_validate(
            {**JOB_SPEC, "fields": [{**JOB_SPEC["fields"][0], "pool": "other"}]}
        )

        assert job_fingerprint(changed) != job_fingerprint(job)

    def test_file_fingerprint_moves_with_the_bytes(self, tmp_path: Path) -> None:
        first = tmp_path / "a.csv"
        first.write_text(INPUT_CSV, encoding="utf-8")
        second = tmp_path / "b.csv"
        second.write_text(INPUT_CSV + "3,Other\n", encoding="utf-8")

        assert file_fingerprint(first) != file_fingerprint(second)


class TestRunStore:
    def test_appended_entries_read_back_in_order(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        store = RunStore.create(tmp_path, build_lock(job, input_file, "0.1.0"))
        store.append(entry("1"))
        store.append(entry("2", "failed", reason="nope"))
        store.close()

        reopened = RunStore.open(store.run_dir)
        assert [line.key for line in reopened.entries()] == ["1", "2"]
        assert reopened.entries()[1].reason == "nope"

    def test_every_line_is_durable_before_the_next_one(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        """A killed run keeps every result it already decided."""
        store = RunStore.create(tmp_path, build_lock(job, input_file, "0.1.0"))
        store.append(entry("1"))

        # No close(): read the file as a crash would leave it.
        written = (store.run_dir / LEDGER_FILENAME).read_text(encoding="utf-8")
        assert '"key":"1"' in written

    def test_completed_excludes_failures_so_a_resume_retries_them(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        store = RunStore.create(tmp_path, build_lock(job, input_file, "0.1.0"))
        store.append(entry("1"))
        store.append(entry("2", "failed", reason="nope"))
        store.append(entry("3", "skipped", reason="guard"))
        store.close()

        done = store.completed()

        assert set(done) == {("1", "severity"), ("3", "severity")}

    def test_job_snapshot_round_trips(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        store = RunStore.create(tmp_path, build_lock(job, input_file, "0.1.0"))
        store.write_job(job)

        assert RunStore.open(store.run_dir).read_job() == job

    def test_report_round_trips(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        store = RunStore.create(tmp_path, build_lock(job, input_file, "0.1.0"))
        store.write_report(
            Report(remuda_version="0.1.0", job_name=job.name, run_id="r", ok=2)
        )

        assert RunStore.open(store.run_dir).read_report().ok == 2

    def test_opening_a_directory_that_holds_no_run_is_refused(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(LedgerError, match="not a run directory"):
            RunStore.open(tmp_path)

    def test_a_corrupt_ledger_line_is_reported_with_its_number(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        store = RunStore.create(tmp_path, build_lock(job, input_file, "0.1.0"))
        store.append(entry("1"))
        store.close()
        with (store.run_dir / LEDGER_FILENAME).open("a", encoding="utf-8") as handle:
            handle.write("{not json}\n")

        with pytest.raises(LedgerError, match="line 2"):
            store.entries()

    def test_runs_are_listed_newest_first(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        lock = build_lock(job, input_file, "0.1.0")
        RunStore.create(tmp_path, lock, run_id="20260101T000000Z")
        RunStore.create(tmp_path, lock, run_id="20260202T000000Z")

        assert [run.name for run in list_runs(tmp_path, job.name)] == [
            "20260202T000000Z",
            "20260101T000000Z",
        ]

    def test_run_id_is_sortable(self) -> None:
        assert len(new_run_id()) == len("20260830T120000Z")


class TestResume:
    def test_an_unchanged_job_resumes_the_latest_run(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        lock = build_lock(job, input_file, "0.1.0")
        first = open_run(tmp_path, lock)
        first.store.append(entry("1"))
        first.store.close()

        second = open_run(tmp_path, build_lock(job, input_file, "0.1.0"))

        assert second.is_resumed is True
        assert second.store.run_dir == first.store.run_dir
        assert set(second.done) == {("1", "severity")}

    def test_a_changed_input_refuses_the_resume(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        open_run(tmp_path, build_lock(job, input_file, "0.1.0")).store.close()
        input_file.write_text(INPUT_CSV + "3,Third\n", encoding="utf-8")

        with pytest.raises(ResumeRefusedError) as exc_info:
            open_run(tmp_path, build_lock(job, input_file, "0.1.0"))

        assert "input file changed" in str(exc_info.value)
        assert "--fresh" in str(exc_info.value)

    def test_a_changed_job_definition_refuses_the_resume(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        open_run(tmp_path, build_lock(job, input_file, "0.1.0")).store.close()
        changed = Job.model_validate(
            {**JOB_SPEC, "fields": [{**JOB_SPEC["fields"][0], "pool": "other"}]}
        )

        with pytest.raises(ResumeRefusedError) as exc_info:
            open_run(tmp_path, build_lock(changed, input_file, "0.1.0"))

        assert "job definition changed" in str(exc_info.value)

    def test_fresh_starts_a_new_run_and_keeps_the_old_one(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        lock = build_lock(job, input_file, "0.1.0")
        first = open_run(tmp_path, lock, fresh=False)
        first.store.append(entry("1"))
        first.store.close()

        second = open_run(tmp_path, build_lock(job, input_file, "0.1.0"), fresh=True)

        assert second.is_resumed is False
        assert second.done == {}
        assert len(list_runs(tmp_path, job.name)) == 2

    def test_a_changed_job_is_accepted_when_starting_fresh(
        self, tmp_path: Path, job: Job, input_file: Path
    ) -> None:
        open_run(tmp_path, build_lock(job, input_file, "0.1.0")).store.close()
        input_file.write_text("id,title\n9,Different\n", encoding="utf-8")

        opened = open_run(tmp_path, build_lock(job, input_file, "0.1.0"), fresh=True)

        assert opened.is_resumed is False


class TestSpecLock:
    def test_mismatches_name_both_moving_parts(self) -> None:
        first = SpecLock(
            remuda_version="0.1.0", job_name="j", job_hash="a", input_hash="b"
        )
        second = SpecLock(
            remuda_version="0.1.0", job_name="j", job_hash="x", input_hash="y"
        )

        assert len(first.mismatches(second)) == 2

    def test_identical_locks_do_not_mismatch(self) -> None:
        lock = SpecLock(
            remuda_version="0.1.0", job_name="j", job_hash="a", input_hash="b"
        )

        assert lock.mismatches(lock) == []
