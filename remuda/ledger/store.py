"""The run directory: lock, crash-safe ledger, report (FR-5, FR-6).

`.runs/<job>/<timestamp>/` holds everything a run knows about itself, so a
report can be re-printed and results re-rendered without calling a model again.
"""

import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

from remuda.catalog.resolve import ResolvedPool
from remuda.errors import RemudaError
from remuda.ledger.models import LedgerEntry, Report, SpecLock
from remuda.spec.models import Job

RUNS_DIRNAME = ".runs"
SPEC_LOCK_FILENAME = "spec.lock.json"
JOB_SNAPSHOT_FILENAME = "job.json"
POOL_RESOLVED_FILENAME = "pool.resolved.json"
LEDGER_FILENAME = "ledger.jsonl"
REPORT_FILENAME = "report.json"

_RUN_ID_FORMAT = "%Y%m%dT%H%M%SZ"


class LedgerError(RemudaError):
    """A run directory could not be read or written."""


def job_fingerprint(job: Job) -> str:
    """A stable hash of the job definition — cosmetic edits move it."""
    payload = job.model_dump_json(by_alias=True)
    canonical = json.dumps(json.loads(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> str:
    """A stable hash of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def new_run_id(now: datetime | None = None) -> str:
    """A sortable run id — the run directory's name."""
    return (now or datetime.now(UTC)).strftime(_RUN_ID_FORMAT)


class RunStore:
    """Reads and writes one run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self._handle: TextIO | None = None

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(
        cls, runs_root: Path, lock: SpecLock, run_id: str | None = None
    ) -> "RunStore":
        """Create a fresh run directory and write its lock."""
        run_dir = _claim_run_dir(
            Path(runs_root) / lock.job_name, run_id or new_run_id()
        )
        store = cls(run_dir)
        store.write_lock(lock)
        return store

    @classmethod
    def open(cls, run_dir: Path) -> "RunStore":
        """Open an existing run directory.

        Raises:
            LedgerError: the directory holds no run.
        """
        directory = Path(run_dir)
        if not (directory / SPEC_LOCK_FILENAME).is_file():
            raise LedgerError(
                f"'{directory}' is not a run directory (no {SPEC_LOCK_FILENAME})"
            )
        return cls(directory)

    @property
    def run_id(self) -> str:
        """The run directory's name."""
        return self.run_dir.name

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the append handle, if one is open."""
        handle = self._handle
        if handle is not None:
            handle.close()
            self._handle = None

    # -- lock --------------------------------------------------------------

    def write_lock(self, lock: SpecLock) -> None:
        """Record what this run was started against."""
        (self.run_dir / SPEC_LOCK_FILENAME).write_text(
            lock.model_dump_json(indent=2), encoding="utf-8"
        )

    def read_lock(self) -> SpecLock:
        """Read what this run was started against.

        Raises:
            LedgerError: the lock is missing or unreadable.
        """
        path = self.run_dir / SPEC_LOCK_FILENAME
        try:
            return SpecLock.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LedgerError(f"cannot read {path}: {error}") from error

    def write_job(self, job: Job) -> None:
        """Snapshot the job the run was started with.

        Rendering a finished run then needs the run directory only — the job
        directory may have moved on (FR-10).
        """
        (self.run_dir / JOB_SNAPSHOT_FILENAME).write_text(
            job.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
        )

    def read_job(self) -> Job:
        """Read the job snapshot this run was started with.

        Raises:
            LedgerError: the snapshot is missing or unreadable.
        """
        path = self.run_dir / JOB_SNAPSHOT_FILENAME
        try:
            return Job.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LedgerError(f"cannot read {path}: {error}") from error

    def write_resolved_pools(self, resolved: Sequence[ResolvedPool]) -> None:
        """Snapshot the pools this run materialized (FR-3).

        A resumed run reuses this rather than re-querying the catalog: free
        tiers churn, and the models that answered must not change mid-run.
        """
        payload = [pool.model_dump(mode="json") for pool in resolved]
        (self.run_dir / POOL_RESOLVED_FILENAME).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def read_resolved_pools(self) -> dict[str, ResolvedPool]:
        """The pools this run materialized, by pool name. Empty when none."""
        path = self.run_dir / POOL_RESOLVED_FILENAME
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pools = [ResolvedPool.model_validate(entry) for entry in payload]
        except (OSError, ValueError) as error:
            raise LedgerError(f"cannot read {path}: {error}") from error
        return {pool.pool: pool for pool in pools}

    # -- ledger ------------------------------------------------------------

    def append(self, entry: LedgerEntry) -> None:
        """Append one decided result, durably.

        Flushed and fsync'd per line: a killed run must lose nothing that was
        already decided, which is what makes resume trustworthy (FR-5).
        """
        handle = self._handle
        if handle is None:
            handle = (self.run_dir / LEDGER_FILENAME).open("a", encoding="utf-8")
            self._handle = handle
        handle.write(entry.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    def entries(self) -> list[LedgerEntry]:
        """Every result recorded so far, in the order it was decided."""
        return list(self._iter_entries())

    def completed(self) -> dict[tuple[str, str], LedgerEntry]:
        """Results a resume must not recompute — ok and skipped only.

        A failed result is deliberately absent: a resumed run retries it.
        """
        done: dict[tuple[str, str], LedgerEntry] = {}
        for entry in self._iter_entries():
            if entry.is_complete:
                done[(entry.key, entry.field)] = entry
        return done

    def _iter_entries(self) -> Iterator[LedgerEntry]:
        path = self.run_dir / LEDGER_FILENAME
        if not path.is_file():
            return
        # Framed on the protocol's real terminator (development/python.md).
        for number, line in enumerate(
            path.read_text(encoding="utf-8").split("\n"), start=1
        ):
            if not line.strip():
                continue
            try:
                yield LedgerEntry.model_validate_json(line)
            except ValueError as error:
                raise LedgerError(
                    f"{path} line {number} is not a ledger entry: {error}"
                ) from error

    # -- report ------------------------------------------------------------

    def write_report(self, report: Report) -> None:
        """Persist the run's account of itself."""
        (self.run_dir / REPORT_FILENAME).write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )

    def read_report(self) -> Report:
        """Re-read a finished run's report.

        Raises:
            LedgerError: the run has no report yet.
        """
        path = self.run_dir / REPORT_FILENAME
        try:
            return Report.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LedgerError(f"cannot read {path}: {error}") from error


def _claim_run_dir(job_root: Path, run_id: str) -> Path:
    """Create a run directory nobody else owns.

    Run ids are second-resolution timestamps, so two runs started in the same
    second would otherwise share a directory — and a fresh run would inherit
    the previous run's ledger. The suffix keeps them separate.
    """
    job_root.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 1000):
        candidate = job_root / (run_id if attempt == 1 else f"{run_id}-{attempt}")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise LedgerError(f"cannot allocate a run directory under {job_root}")


def list_runs(runs_root: Path, job_name: str | None = None) -> list[Path]:
    """Every run directory, newest first."""
    root = Path(runs_root)
    if not root.is_dir():
        return []
    job_dirs = [root / job_name] if job_name else sorted(root.iterdir())
    runs = [
        run
        for job_dir in job_dirs
        if job_dir.is_dir()
        for run in job_dir.iterdir()
        if (run / SPEC_LOCK_FILENAME).is_file()
    ]
    return sorted(runs, key=lambda path: path.name, reverse=True)


def latest_run(runs_root: Path, job_name: str) -> Path | None:
    """The most recent run directory for a job, if there is one."""
    runs = list_runs(runs_root, job_name)
    return runs[0] if runs else None
