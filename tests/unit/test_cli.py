"""FR-8 — the zero-network CLI surface: check, preview, init.

Every assertion here also proves the stream contract: diagnostics on stderr,
data on stdout. The autouse socket guard in conftest proves no command in
this phase reaches the network.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from remuda.cli.app import app
from remuda.spec.lint import PLACEHOLDER

runner = CliRunner()

LOCAL_PROVIDER = {"local": {"base_url": "http://localhost:11434/v1"}}
LOCAL_MODEL = {"small": {"provider": "local", "model_id": "qwen3:4b"}}
LOCAL_POOL = {"cheap": ["small"]}

INPUT_CSV = "post_id,title,body\n1,Leak,Water everywhere\n2,Noise,Loud music\n"

JOB_SPEC: dict[str, Any] = {
    "name": "rally-severity",
    "input": {"path": "posts.csv", "key": "post_id"},
    "fields": [
        {
            "name": "severity",
            "kind": "classify",
            "pool": "cheap",
            "vocabulary": ["high", "low"],
            "prompt": {
                "inputs": ["title", "body"],
                "template": "Title: {{ title }}\nBody: {{ body }}\nSeverity?",
            },
        }
    ],
}


@pytest.fixture
def registry_dir(write_registry_dir: Callable[..., Path]) -> Path:
    return write_registry_dir(
        providers=LOCAL_PROVIDER, models=LOCAL_MODEL, pools=LOCAL_POOL
    )


class TestCheck:
    def test_runnable_job_exits_zero_and_writes_nothing_to_stdout(
        self, write_job_dir: Any, registry_dir: Path
    ) -> None:
        job_dir = write_job_dir(JOB_SPEC, {"posts.csv": INPUT_CSV})

        result = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(registry_dir)]
        )

        assert result.exit_code == 0, result.stderr
        assert result.stdout == ""
        assert "runnable" in result.stderr

    def test_unknown_pool_is_refused_naming_the_pool(
        self, write_job_dir: Any, registry_dir: Path
    ) -> None:
        spec = {**JOB_SPEC, "fields": [{**JOB_SPEC["fields"][0], "pool": "ghost"}]}
        job_dir = write_job_dir(spec, {"posts.csv": INPUT_CSV})

        result = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(registry_dir)]
        )

        assert result.exit_code == 1
        assert "ghost" in result.stderr
        assert "severity" in result.stderr

    def test_prompt_input_absent_from_the_input_file_is_refused(
        self, write_job_dir: Any, registry_dir: Path
    ) -> None:
        job_dir = write_job_dir(JOB_SPEC, {"posts.csv": "post_id,title\n1,Leak\n"})

        result = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(registry_dir)]
        )

        assert result.exit_code == 1
        assert "body" in result.stderr

    def test_missing_input_file_is_refused(
        self, write_job_dir: Any, registry_dir: Path
    ) -> None:
        job_dir = write_job_dir(JOB_SPEC)

        result = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(registry_dir)]
        )

        assert result.exit_code == 1
        assert "posts.csv" in result.stderr

    def test_spec_defect_is_reported_without_consulting_the_registry(
        self, write_job_dir: Any, tmp_path: Path
    ) -> None:
        spec = {
            **JOB_SPEC,
            "fields": [{**JOB_SPEC["fields"][0], "kind": "classifyy"}],
        }
        job_dir = write_job_dir(spec, {"posts.csv": INPUT_CSV})

        result = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(tmp_path / "absent")]
        )

        assert result.exit_code == 1
        assert "classifyy" in result.stderr


class TestPreview:
    def test_prompts_go_to_stdout_and_diagnostics_to_stderr(
        self, write_job_dir: Any
    ) -> None:
        job_dir = write_job_dir(JOB_SPEC, {"posts.csv": INPUT_CSV})

        result = runner.invoke(app, ["preview", str(job_dir), "--rows", "1"])

        assert result.exit_code == 0, result.stderr
        assert "Title: Leak" in result.stdout
        assert "Body: Water everywhere" in result.stdout
        assert "zero model calls" in result.stderr
        assert "zero model calls" not in result.stdout

    def test_row_limit_is_honoured(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(JOB_SPEC, {"posts.csv": INPUT_CSV})

        result = runner.invoke(app, ["preview", str(job_dir), "-n", "2"])

        assert result.stdout.count(":: severity ---") == 2

    def test_unknown_field_selection_is_refused(self, write_job_dir: Any) -> None:
        job_dir = write_job_dir(JOB_SPEC, {"posts.csv": INPUT_CSV})

        result = runner.invoke(app, ["preview", str(job_dir), "-F", "nope"])

        assert result.exit_code == 1
        assert "nope" in result.stderr

    def test_misdeclared_job_is_refused_before_reading_rows(
        self, write_job_dir: Any
    ) -> None:
        spec = {
            **JOB_SPEC,
            "fields": [
                {
                    **JOB_SPEC["fields"][0],
                    "prompt": {
                        "inputs": ["title"],
                        "template": "{{ title }} {{ author }}",
                    },
                }
            ],
        }
        job_dir = write_job_dir(spec, {"posts.csv": INPUT_CSV})

        result = runner.invoke(app, ["preview", str(job_dir)])

        assert result.exit_code == 1
        assert "author" in result.stderr


class TestInitRoundTrip:
    def test_scaffold_is_refused_until_placeholders_are_filled(
        self, tmp_path: Path, registry_dir: Path
    ) -> None:
        job_dir = tmp_path / "scaffolded"

        created = runner.invoke(app, ["init", str(job_dir)])
        assert created.exit_code == 0, created.stderr

        pending = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(registry_dir)]
        )
        assert pending.exit_code == 1
        assert PLACEHOLDER in pending.stderr

        _fill_placeholders(job_dir, pool="cheap")

        filled = runner.invoke(
            app, ["check", str(job_dir), "--config-dir", str(registry_dir)]
        )
        assert filled.exit_code == 0, filled.stderr

    def test_scaffolded_job_previews_without_a_registry(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "scaffolded"
        runner.invoke(app, ["init", str(job_dir)])
        _fill_placeholders(job_dir, pool="cheap")

        result = runner.invoke(app, ["preview", str(job_dir), "-n", "1"])

        assert result.exit_code == 0, result.stderr
        assert "The first example row" in result.stdout

    def test_init_refuses_to_overwrite_an_existing_job(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "scaffolded"
        runner.invoke(app, ["init", str(job_dir)])

        second = runner.invoke(app, ["init", str(job_dir)])

        assert second.exit_code == 1
        assert "already exists" in second.stderr

    def test_job_name_defaults_to_the_directory_name(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "severity-backfill"
        runner.invoke(app, ["init", str(job_dir)])

        assert "name: severity-backfill" in (job_dir / "job.yaml").read_text()


class TestVersion:
    def test_version_is_reported_on_stderr(self) -> None:
        result = runner.invoke(app, ["version"])

        assert result.exit_code == 0
        assert "remuda" in result.stderr
        assert result.stdout == ""


def _fill_placeholders(job_dir: Path, pool: str) -> None:
    """Do what the operator does after `remuda init`."""
    job_file = job_dir / "job.yaml"
    job_file.write_text(
        job_file.read_text(encoding="utf-8").replace(f"{PLACEHOLDER}_POOL", pool),
        encoding="utf-8",
    )
    labels = job_dir / "vocab" / "labels.txt"
    labels.write_text(
        labels.read_text(encoding="utf-8")
        .replace(f"{PLACEHOLDER}_LABEL_ONE", "urgent")
        .replace(f"{PLACEHOLDER}_LABEL_TWO", "routine"),
        encoding="utf-8",
    )
