"""End-to-end: a job directory, a live endpoint, a run, and an enriched CSV.

Covers the FR-4 ladder over real HTTP, the FR-5 resume, and the FR-10 enrich
render — through the CLI the operator actually types.
"""

import csv
import io
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from remuda.cli.app import app
from remuda.ledger.store import LEDGER_FILENAME, list_runs
from tests.integration.conftest import FakeModelServer

pytestmark = pytest.mark.integration

runner = CliRunner()

INPUT_CSV = "post_id,title,body,severity\n1,Leak,Water everywhere,\n2,Noise,Loud,\n"

JOB: dict[str, Any] = {
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
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "job"
    directory.mkdir()
    (directory / "job.yaml").write_text(yaml.safe_dump(JOB, sort_keys=False))
    (directory / "posts.csv").write_text(INPUT_CSV, encoding="utf-8")
    return directory


@pytest.fixture
def config_dir(tmp_path: Path, model_server: FakeModelServer) -> Path:
    directory = tmp_path / "registry"
    directory.mkdir()
    (directory / "providers.yaml").write_text(
        yaml.safe_dump({"local": {"base_url": model_server.base_url}})
    )
    (directory / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "a": {"provider": "local", "model_id": "a"},
                "b": {"provider": "local", "model_id": "b"},
            }
        )
    )
    (directory / "pools.yaml").write_text(
        yaml.safe_dump({"cheap": {"strategy": "waterfall", "models": ["a", "b"]}})
    )
    return directory


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


def run_job(job_dir: Path, config_dir: Path, *extra: str) -> Any:
    return invoke("run", str(job_dir), "--config-dir", str(config_dir), *extra)


def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


class TestRunAndRender:
    def test_a_clean_run_exits_zero_and_records_every_result(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"

        result = run_job(job_dir, config_dir)

        assert result.exit_code == 0, result.stderr
        assert "ok 1" in result.stderr  # per-chunk progress reached stderr
        run = list_runs(job_dir / ".runs", "rally-severity")[0]
        assert (run / LEDGER_FILENAME).read_text(encoding="utf-8").count("\n") == 2

    def test_the_ladder_walks_invalid_then_rate_limited_then_valid(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        """Invalid answer → retry, 429 → cool down and rotate, then valid."""
        model_server.script = {
            "a": [
                "not-a-label",
                (429, {"error": {"message": "slow down"}}),
            ],
            "b": ["high"],
        }
        model_server.default = "low"

        result = run_job(job_dir, config_dir, "--limit", "1")

        assert result.exit_code == 0, result.stderr
        assert model_server.prompts_for("a")[1].count("previous answer was rejected")
        assert model_server.prompts_for("b")
        assert "cooling down" in result.stderr

    def test_enrich_returns_the_input_with_one_column_filled(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"
        run_job(job_dir, config_dir)
        run = list_runs(job_dir / ".runs", "rally-severity")[0]

        rendered = invoke("render", str(run), "--enrich")

        assert rendered.exit_code == 0, rendered.stderr
        columns, records = parse_csv(rendered.stdout)
        assert columns == ["post_id", "title", "body", "severity"]
        assert [record["severity"] for record in records] == ["high", "high"]
        assert [record["title"] for record in records] == ["Leak", "Noise"]

    def test_rendering_calls_no_model(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"
        run_job(job_dir, config_dir)
        run = list_runs(job_dir / ".runs", "rally-severity")[0]
        calls_after_run = len(model_server.requests)

        invoke("render", str(run), "-f", "jsonl")

        assert len(model_server.requests) == calls_after_run

    def test_render_writes_to_a_file_when_asked(
        self,
        job_dir: Path,
        config_dir: Path,
        model_server: FakeModelServer,
        tmp_path: Path,
    ) -> None:
        model_server.default = "high"
        run_job(job_dir, config_dir)
        run = list_runs(job_dir / ".runs", "rally-severity")[0]
        target = tmp_path / "enriched.csv"

        result = invoke("render", str(run), "--enrich", "-o", str(target))

        assert result.exit_code == 0
        assert result.stdout == ""
        assert "severity" in target.read_text(encoding="utf-8")

    def test_report_is_reprintable_without_re_running(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"
        run_job(job_dir, config_dir)
        run = list_runs(job_dir / ".runs", "rally-severity")[0]

        printed = invoke("report", str(run), "--json")

        assert printed.exit_code == 0
        assert '"ok": 2' in printed.stdout


class TestFailureContract:
    def test_a_run_with_a_failed_key_exits_non_zero(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "not-a-label"

        result = run_job(job_dir, config_dir, "--limit", "1")

        assert result.exit_code == 1
        assert "failed" in result.stderr

    def test_a_permanently_failing_provider_fails_the_run_with_the_reason(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = (401, {"error": {"message": "bad key"}})

        result = run_job(job_dir, config_dir, "--limit", "1")

        assert result.exit_code == 1
        assert "bad key" in result.stderr


class TestResume:
    def test_a_second_run_reuses_completed_results_and_retries_failures(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.script = {"a": ["high", "junk", "junk"], "b": ["junk", "junk"]}
        model_server.default = "junk"

        first = run_job(job_dir, config_dir)
        assert first.exit_code == 1  # key 2 never got a valid answer

        model_server.script = {}
        model_server.default = "low"
        model_server.requests.clear()

        second = run_job(job_dir, config_dir)

        assert second.exit_code == 0, second.stderr
        assert "resumed run" in second.stderr
        # Only the failed key was re-asked.
        assert all(
            "Noise" in sent["messages"][-1]["content"] for sent in model_server.requests
        )
        assert len(list_runs(job_dir / ".runs", "rally-severity")) == 1

    def test_a_changed_input_refuses_the_resume_and_names_the_option(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "not-a-label"
        run_job(job_dir, config_dir, "--limit", "1")
        (job_dir / "posts.csv").write_text(
            INPUT_CSV + "3,Third,More,\n", encoding="utf-8"
        )

        refused = run_job(job_dir, config_dir)

        assert refused.exit_code == 1
        assert "input file changed" in refused.stderr
        assert "--fresh" in refused.stderr

    def test_fresh_starts_a_second_run_directory(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"
        run_job(job_dir, config_dir)

        second = run_job(job_dir, config_dir, "--fresh")

        assert second.exit_code == 0, second.stderr
        assert len(list_runs(job_dir / ".runs", "rally-severity")) == 2


class TestPacking:
    def test_packed_calls_answer_several_rows_at_once(
        self, job_dir: Path, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        packed = {**JOB, "execution": {"records_per_call": 2}}
        (job_dir / "job.yaml").write_text(yaml.safe_dump(packed, sort_keys=False))
        model_server.default = "1: high\n2: low"

        result = run_job(job_dir, config_dir)

        assert result.exit_code == 0, result.stderr
        assert len(model_server.requests) == 1
        run = list_runs(job_dir / ".runs", "rally-severity")[0]
        rendered = invoke("render", str(run), "-f", "csv")
        _, records = parse_csv(rendered.stdout)
        assert [record["severity"] for record in records] == ["high", "low"]
