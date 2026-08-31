"""FR-11/FR-13/FR-14 at the command line, against the loopback server."""

import csv
import io
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from remuda.cli.app import app
from remuda.ledger.store import RUNS_DIRNAME, list_runs
from tests.integration.conftest import FakeModelServer

pytestmark = pytest.mark.integration

runner = CliRunner()

INPUT_CSV = "post_id,title,severity\n1,Leak,\n2,Noise,\n3,Party,\n"


@pytest.fixture
def config_dir(tmp_path: Path, model_server: FakeModelServer) -> Path:
    directory = tmp_path / "registry"
    directory.mkdir()
    (directory / "providers.yaml").write_text(
        yaml.safe_dump({"local": {"base_url": model_server.base_url}})
    )
    (directory / "models.yaml").write_text(
        yaml.safe_dump({"a": {"provider": "local", "model_id": "a"}})
    )
    (directory / "pools.yaml").write_text(yaml.safe_dump({"cheap": ["a"]}))
    return directory


@pytest.fixture
def input_file(tmp_path: Path) -> Path:
    path = tmp_path / "posts.csv"
    path.write_text(INPUT_CSV, encoding="utf-8")
    return path


def parse_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


class TestOneShot:
    def test_only_the_answer_reaches_stdout(
        self, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "Madrid"

        result = runner.invoke(
            app,
            ["run", "Name the capital of Spain", "-p", "cheap", "-c", str(config_dir)],
        )

        assert result.exit_code == 0, result.stderr
        assert result.stdout == "Madrid\n"

    def test_a_vocabulary_constrains_the_answer(
        self, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "  HIGH  "

        result = runner.invoke(
            app,
            [
                "run",
                "Severity?",
                "-p",
                "cheap",
                "-v",
                "high,low",
                "-c",
                str(config_dir),
            ],
        )

        assert result.exit_code == 0, result.stderr
        assert result.stdout == "high\n"  # normalized to the declared label

    def test_a_pool_that_never_answers_validly_exits_non_zero(
        self, config_dir: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "banana"

        result = runner.invoke(
            app,
            [
                "run",
                "Severity?",
                "-p",
                "cheap",
                "-v",
                "high,low",
                "-c",
                str(config_dir),
            ],
        )

        assert result.exit_code == 1
        assert result.stdout == ""  # nothing invalid ever reaches the answer stream
        assert "no valid answer" in result.stderr

    def test_a_one_shot_without_a_pool_says_so(self, config_dir: Path) -> None:
        result = runner.invoke(app, ["run", "Anything", "-c", str(config_dir)])

        assert result.exit_code == 1
        assert "--pool" in result.stderr

    def test_a_one_shot_writes_no_run_directory(
        self, config_dir: Path, model_server: FakeModelServer, tmp_path: Path
    ) -> None:
        model_server.default = "Madrid"

        runner.invoke(app, ["run", "Capital?", "-p", "cheap", "-c", str(config_dir)])

        assert list_runs(tmp_path / RUNS_DIRNAME) == []


class TestInlineBulk:
    def test_csv_in_enriched_csv_out(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"

        result = runner.invoke(
            app,
            [
                "run",
                "Severity of {{ title }}?",
                "-i",
                str(input_file),
                "--field",
                "severity",
                "-v",
                "high,low",
                "-p",
                "cheap",
                "-c",
                str(config_dir),
            ],
        )

        assert result.exit_code == 0, result.stderr
        columns, records = parse_csv(result.stdout)
        assert columns == ["post_id", "title", "severity"]
        assert [record["severity"] for record in records] == ["high"] * 3
        assert [record["title"] for record in records] == ["Leak", "Noise", "Party"]

    def test_the_result_can_be_written_to_a_file(
        self,
        config_dir: Path,
        input_file: Path,
        model_server: FakeModelServer,
        tmp_path: Path,
    ) -> None:
        model_server.default = "low"
        target = tmp_path / "enriched.csv"

        result = runner.invoke(
            app,
            [
                "run",
                "Severity of {{ title }}?",
                "-i",
                str(input_file),
                "--field",
                "severity",
                "-p",
                "cheap",
                "-o",
                str(target),
                "-c",
                str(config_dir),
            ],
        )

        assert result.exit_code == 0, result.stderr
        assert result.stdout == ""
        assert "low" in target.read_text(encoding="utf-8")

    def test_a_prompt_reading_a_missing_column_is_refused(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        result = runner.invoke(
            app,
            [
                "run",
                "Severity of {{ author }}?",
                "-i",
                str(input_file),
                "-p",
                "cheap",
                "-c",
                str(config_dir),
            ],
        )

        assert result.exit_code == 1
        assert "author" in result.stderr
        assert len(model_server.requests) == 0

    def test_an_inline_run_is_resumable(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        """FR-13: the ledger and resume semantics of a job run still hold."""
        model_server.answers = {"Leak": "high", "Noise": "junk", "Party": "junk"}
        command = [
            "run",
            "Severity of {{ title }}?",
            "-i",
            str(input_file),
            "--field",
            "severity",
            "-v",
            "high,low",
            "-p",
            "cheap",
            "-c",
            str(config_dir),
        ]

        first = runner.invoke(app, command)
        assert first.exit_code == 1

        model_server.answers = {}
        model_server.default = "low"
        model_server.requests.clear()

        second = runner.invoke(app, command)

        assert second.exit_code == 0, second.stderr
        _, records = parse_csv(second.stdout)
        assert [record["severity"] for record in records] == ["high", "low", "low"]
        titles = {
            request["messages"][-1]["content"] for request in model_server.requests
        }
        assert not any("Leak" in prompt for prompt in titles)  # already done

    def test_the_run_is_recorded_like_any_other(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"

        runner.invoke(
            app,
            [
                "run",
                "Severity of {{ title }}?",
                "-i",
                str(input_file),
                "-p",
                "cheap",
                "-c",
                str(config_dir),
            ],
        )

        runs = list_runs(input_file.parent / RUNS_DIRNAME)
        assert len(runs) == 1
        report = runner.invoke(app, ["report", str(runs[0]), "--json"])
        assert report.exit_code == 0
        assert '"ok": 3' in report.stdout


class TestStatsAndRuns:
    def run_twice(
        self, config_dir: Path, input_file: Path, server: FakeModelServer
    ) -> Path:
        server.default = "high"
        for _ in range(2):
            runner.invoke(
                app,
                [
                    "run",
                    "Severity of {{ title }}?",
                    "-i",
                    str(input_file),
                    "-p",
                    "cheap",
                    "-c",
                    str(config_dir),
                    "--fresh",
                ],
            )
        return input_file.parent / RUNS_DIRNAME

    def test_stats_rank_models_across_runs(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        runs_dir = self.run_twice(config_dir, input_file, model_server)

        result = runner.invoke(app, ["stats", "--runs-dir", str(runs_dir)])

        assert result.exit_code == 0, result.stderr
        assert "Model performance across 2 run(s)" in result.stdout
        assert "1 model(s) across 2 run(s)" in result.stderr
        assert "never reorders a pool" in result.stderr

    def test_runs_are_listed_newest_first(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        runs_dir = self.run_twice(config_dir, input_file, model_server)

        result = runner.invoke(app, ["runs", "list", "--runs-dir", str(runs_dir)])

        assert result.exit_code == 0, result.stderr
        assert "2 run(s)" in result.stderr

    def test_prune_is_dry_by_default(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        runs_dir = self.run_twice(config_dir, input_file, model_server)

        result = runner.invoke(
            app, ["runs", "prune", "--keep", "1", "--runs-dir", str(runs_dir)]
        )

        assert result.exit_code == 0
        assert "dry run" in result.stderr
        assert len(list_runs(runs_dir)) == 2  # nothing removed

    def test_prune_write_removes_the_oldest(
        self, config_dir: Path, input_file: Path, model_server: FakeModelServer
    ) -> None:
        runs_dir = self.run_twice(config_dir, input_file, model_server)
        before = [run.name for run in list_runs(runs_dir)]

        result = runner.invoke(
            app,
            ["runs", "prune", "--keep", "1", "--runs-dir", str(runs_dir), "--write"],
        )

        assert result.exit_code == 0, result.stderr
        remaining = [run.name for run in list_runs(runs_dir)]
        assert remaining == [before[0]]

    def test_stats_over_an_empty_directory_says_so(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["stats", "--runs-dir", str(tmp_path / "none")])

        assert result.exit_code == 1
        assert "no finished runs" in result.stderr
