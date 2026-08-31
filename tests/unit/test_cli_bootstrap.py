"""FR-3b at the command line: announced, overridable, and opt-out-able."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from remuda.cli import options
from remuda.cli.app import app
from remuda.registry.bootstrap import DISABLE_ENV, OPENROUTER_KEY_ENV

runner = CliRunner()

JOB: dict[str, Any] = {
    "name": "bootstrapped",
    "input": {"path": "in.csv", "key": "id"},
    "fields": [
        {
            "name": "severity",
            "kind": "classify",
            "pool": "free",
            "vocabulary": ["high", "low"],
            "prompt": {"inputs": ["title"], "template": "{{ title }}"},
        }
    ],
}


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "job"
    directory.mkdir()
    (directory / "job.yaml").write_text(yaml.safe_dump(JOB, sort_keys=False))
    (directory / "in.csv").write_text("id,title\n1,Leak\n", encoding="utf-8")
    return directory


@pytest.fixture
def bootstrapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment that implies an OpenRouter provider, with no local probe."""
    monkeypatch.delenv(DISABLE_ENV, raising=False)
    monkeypatch.setenv(OPENROUTER_KEY_ENV, "sk-test")
    monkeypatch.setattr(options, "probe_local_endpoint", lambda: False)


def empty_config(tmp_path: Path) -> Path:
    directory = tmp_path / "empty"
    directory.mkdir()
    return directory


class TestImplicitRegistry:
    def test_a_job_naming_the_implicit_pool_checks_out_with_no_config_files(
        self, job_dir: Path, tmp_path: Path, bootstrapping: None
    ) -> None:
        result = runner.invoke(
            app, ["check", str(job_dir), "-c", str(empty_config(tmp_path))]
        )

        assert result.exit_code == 0, result.stderr
        assert "runnable" in result.stderr

    def test_the_implicit_resolution_is_announced(
        self, job_dir: Path, tmp_path: Path, bootstrapping: None
    ) -> None:
        result = runner.invoke(
            app, ["check", str(job_dir), "-c", str(empty_config(tmp_path))]
        )

        assert OPENROUTER_KEY_ENV in result.stderr
        assert "implicit" in result.stderr
        assert result.stdout == ""  # announcements never touch the answer stream

    def test_no_bootstrap_leaves_the_pool_unknown(
        self, job_dir: Path, tmp_path: Path, bootstrapping: None
    ) -> None:
        result = runner.invoke(
            app,
            [
                "check",
                str(job_dir),
                "-c",
                str(empty_config(tmp_path)),
                "--no-bootstrap",
            ],
        )

        assert result.exit_code == 1
        assert "unknown pool 'free'" in result.stderr

    def test_the_disable_variable_also_stops_it(
        self,
        job_dir: Path,
        tmp_path: Path,
        bootstrapping: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(DISABLE_ENV, "1")

        result = runner.invoke(
            app, ["check", str(job_dir), "-c", str(empty_config(tmp_path))]
        )

        assert result.exit_code == 1
        assert OPENROUTER_KEY_ENV not in result.stderr

    def test_nothing_is_announced_when_the_environment_implies_nothing(
        self, job_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DISABLE_ENV, raising=False)
        monkeypatch.delenv(OPENROUTER_KEY_ENV, raising=False)
        monkeypatch.setattr(options, "probe_local_endpoint", lambda: False)

        result = runner.invoke(
            app, ["check", str(job_dir), "-c", str(empty_config(tmp_path))]
        )

        assert result.exit_code == 1
        assert "implicit" not in result.stderr


class TestExplicitOverride:
    def test_an_explicit_pool_of_the_same_name_is_the_one_used(
        self, job_dir: Path, tmp_path: Path, bootstrapping: None
    ) -> None:
        """The implicit 'free' pool would check out; the explicit one must not."""
        config = tmp_path / "registry"
        config.mkdir()
        (config / "pools.yaml").write_text(yaml.safe_dump({"free": ["mine"]}))

        result = runner.invoke(app, ["check", str(job_dir), "-c", str(config)])

        assert result.exit_code == 1
        assert "unknown model 'mine'" in result.stderr
        assert OPENROUTER_KEY_ENV in result.stderr  # still announced
