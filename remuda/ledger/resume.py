"""Resuming a stopped run, or refusing to (FR-5).

A resume is only safe when the job definition AND the input are unchanged.
When either moved, remuda refuses and names the fresh-start option rather
than silently mixing results from two different definitions.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from remuda.errors import RemudaError
from remuda.ledger.models import LedgerEntry, SpecLock
from remuda.ledger.store import (
    RunStore,
    file_fingerprint,
    job_fingerprint,
    latest_run,
)
from remuda.spec.models import Job


class ResumeRefusedError(RemudaError):
    """The latest run cannot be resumed against the current job or input."""

    def __init__(self, run_dir: Path, reasons: Sequence[str]) -> None:
        self.run_dir = run_dir
        self.reasons: tuple[str, ...] = tuple(reasons)
        super().__init__(self._describe())

    def _describe(self) -> str:
        lines = [f"cannot resume the run at {self.run_dir}:"]
        lines.extend(f"  - {reason}" for reason in self.reasons)
        lines.append(
            "  Start a fresh run with --fresh (the existing run is kept), or "
            "restore the previous job definition and input to resume."
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class OpenedRun:
    """The run to write into, and what it already decided."""

    store: RunStore
    done: dict[tuple[str, str], LedgerEntry]
    is_resumed: bool


def build_lock(
    job: Job, input_path: Path | None, version: str, rows_hash: str | None = None
) -> SpecLock:
    """Fingerprint the job and its input for this run."""
    if input_path is not None:
        input_hash = file_fingerprint(input_path)
    else:
        input_hash = rows_hash or "in-memory"
    return SpecLock(
        remuda_version=version,
        job_name=job.name,
        job_hash=job_fingerprint(job),
        input_hash=input_hash,
        input_path=str(input_path) if input_path else None,
    )


def open_run(
    runs_root: Path,
    lock: SpecLock,
    fresh: bool = False,
) -> OpenedRun:
    """Resume the latest matching run, or start a new one.

    Raises:
        ResumeRefusedError: the latest run was started against a different
            job definition or input.
    """
    if not fresh:
        previous = latest_run(runs_root, lock.job_name)
        if previous is not None:
            store = RunStore.open(previous)
            differences = store.read_lock().mismatches(lock)
            if differences:
                raise ResumeRefusedError(previous, differences)
            done = store.completed()
            if done:
                return OpenedRun(store=store, done=done, is_resumed=True)
            return OpenedRun(store=store, done={}, is_resumed=True)
    return OpenedRun(store=RunStore.create(runs_root, lock), done={}, is_resumed=False)
