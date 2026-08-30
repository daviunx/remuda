"""FR-1 — job definition loading and refusal.

A misdeclared job directory is rejected with the specific defect named, BEFORE any
model is called; the fixed job passes the same check.

Scope note: this module covers the defects `Job.from_dir()` can decide on its own —
unknown field kind, a prompt template referencing a variable outside the declared
`prompt.inputs` allowlist, and a missing vocabulary file. The remaining FR-1 defect
class (unknown pool/model name) needs the registry to resolve names and is covered by
the registry-aware `check` tests, not here.
"""

from pathlib import Path

import pytest
import yaml

from remuda.spec import Job, SpecValidationError

VALID_JOB: dict[str, object] = {
    "name": "rally-severity",
    "input": {"path": "posts.csv", "key": "post_id"},
    "fields": [
        {
            "name": "severity",
            "kind": "classify",
            "pool": "free-fast",
            "vocabulary_file": "vocab/severity.txt",
            "prompt": {
                "inputs": ["title", "body"],
                "template": "Title: {{ title }}\nBody: {{ body }}\nSeverity?",
            },
        }
    ],
}

SEVERITY_VOCABULARY = "critical\nhigh\nmedium\nlow\n"


def write_job(root: Path, spec: dict[str, object], *, vocabulary: str | None) -> Path:
    """Write a job directory; omit the vocabulary file when `vocabulary` is None."""
    job_dir = root / "job"
    (job_dir / "vocab").mkdir(parents=True)
    (job_dir / "job.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    (job_dir / "posts.csv").write_text("post_id,title,body\n1,Leak,Water everywhere\n")
    if vocabulary is not None:
        (job_dir / "vocab" / "severity.txt").write_text(vocabulary)
    return job_dir


def mutate_field(**overrides: object) -> dict[str, object]:
    """Copy VALID_JOB with the single field's keys overridden."""
    field = {**VALID_JOB["fields"][0], **overrides}  # type: ignore[index,dict-item]
    return {**VALID_JOB, "fields": [field]}


class TestJobFromDirAcceptsValidJob:
    def test_valid_job_dir_loads(self, tmp_path: Path) -> None:
        job_dir = write_job(tmp_path, VALID_JOB, vocabulary=SEVERITY_VOCABULARY)

        job = Job.from_dir(job_dir)

        assert job.name == "rally-severity"
        assert job.input.key == "post_id"
        assert [f.name for f in job.fields] == ["severity"]

        severity = job.fields[0]
        assert severity.kind == "classify"
        assert severity.pool == "free-fast"
        assert list(severity.vocabulary) == ["critical", "high", "medium", "low"]
        assert list(severity.prompt.inputs) == ["title", "body"]


class TestJobFromDirRefusesMisdeclaredJob:
    def test_unknown_field_kind_names_the_field_and_the_bad_kind(
        self, tmp_path: Path
    ) -> None:
        job_dir = write_job(
            tmp_path,
            mutate_field(kind="classifyy"),
            vocabulary=SEVERITY_VOCABULARY,
        )

        with pytest.raises(SpecValidationError) as exc_info:
            Job.from_dir(job_dir)

        message = str(exc_info.value)
        assert "severity" in message, message
        assert "classifyy" in message, message
        assert "kind" in message.lower(), message
        # The refusal must be actionable: it names what IS accepted.
        assert "extract" in message and "generate" in message, message

    def test_template_variable_outside_declared_inputs_names_both(
        self, tmp_path: Path
    ) -> None:
        job_dir = write_job(
            tmp_path,
            mutate_field(
                prompt={
                    "inputs": ["title", "body"],
                    "template": (
                        "Title: {{ title }}\nBody: {{ body }}\n"
                        "Author: {{ author }}\nSeverity?"
                    ),
                }
            ),
            vocabulary=SEVERITY_VOCABULARY,
        )

        with pytest.raises(SpecValidationError) as exc_info:
            Job.from_dir(job_dir)

        message = str(exc_info.value)
        assert "severity" in message, message
        assert "author" in message, message
        assert "inputs" in message.lower(), message
        assert "template" in message.lower(), message

    def test_missing_vocabulary_file_names_the_field_and_the_path(
        self, tmp_path: Path
    ) -> None:
        job_dir = write_job(tmp_path, VALID_JOB, vocabulary=None)

        with pytest.raises(SpecValidationError) as exc_info:
            Job.from_dir(job_dir)

        message = str(exc_info.value)
        assert "severity" in message, message
        assert "vocab/severity.txt" in message, message

    def test_fixing_the_defect_makes_the_same_job_dir_load(
        self, tmp_path: Path
    ) -> None:
        """FR-1 acceptance: the fixed job passes the same check."""
        job_dir = write_job(tmp_path, VALID_JOB, vocabulary=None)

        with pytest.raises(SpecValidationError):
            Job.from_dir(job_dir)

        (job_dir / "vocab" / "severity.txt").write_text(SEVERITY_VOCABULARY)

        job = Job.from_dir(job_dir)
        assert list(job.fields[0].vocabulary) == ["critical", "high", "medium", "low"]
