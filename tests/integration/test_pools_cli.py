"""FR-3/FR-12 — `pools show`, `pools check` and `models list` over real HTTP."""

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from typer.testing import CliRunner

from remuda.cli.app import app
from remuda.ledger.store import POOL_RESOLVED_FILENAME, list_runs
from tests.integration.conftest import FakeModelServer

pytestmark = pytest.mark.integration

runner = CliRunner()

CATALOG = {
    "data": [
        {
            "id": "free-fast",
            "context_length": 65536,
            "pricing": {"prompt": "0", "completion": "0"},
            "stats": {"throughput": 120.0},
        },
        {
            "id": "free-slow",
            "context_length": 8192,
            "pricing": {"prompt": "0", "completion": "0"},
            "stats": {"throughput": 20.0},
        },
        {
            "id": "paid-one",
            "context_length": 128000,
            "pricing": {"prompt": "0.000002", "completion": "0.000008"},
            "stats": {"throughput": 200.0},
        },
    ]
}


@pytest.fixture
def catalog_server(model_server: FakeModelServer) -> FakeModelServer:
    """The loopback server also answers `GET /v1/models` from CATALOG."""
    model_server.catalog_payload = CATALOG
    return model_server


def write_registry(
    directory: Path, server: FakeModelServer, pools: dict[str, Any]
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "providers.yaml").write_text(
        yaml.safe_dump(
            {"local": {"base_url": server.base_url, "catalog": "openai_compat"}}
        )
    )
    (directory / "models.yaml").write_text(
        yaml.safe_dump({"pinned": {"provider": "local", "model_id": "pinned"}})
    )
    (directory / "pools.yaml").write_text(yaml.safe_dump(pools))
    return directory


class TestPoolsShow:
    def test_declared_membership_is_printed_to_stdout(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        config = write_registry(
            tmp_path / "registry", catalog_server, {"cheap": ["pinned"]}
        )

        result = runner.invoke(app, ["pools", "show", "cheap", "-c", str(config)])

        assert result.exit_code == 0, result.stderr
        assert "pinned" in result.stdout
        assert "declared" in result.stdout

    def test_a_discovery_pool_is_materialized_from_the_live_catalog(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        config = write_registry(
            tmp_path / "registry",
            catalog_server,
            {
                "discovered": {
                    "entries": [
                        {"discover": {"provider": "local", "include": ["free"]}}
                    ]
                }
            },
        )

        result = runner.invoke(app, ["pools", "show", "discovered", "-c", str(config)])

        assert result.exit_code == 0, result.stderr
        assert "free-fast" in result.stdout
        assert "discovered" in result.stdout

    def test_an_unanswerable_filter_is_refused_naming_it(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        """The loopback catalog is name-only; a pricing filter must refuse."""
        config = write_registry(
            tmp_path / "registry",
            catalog_server,
            {
                "impossible": {
                    "entries": [{"discover": {"provider": "local", "free": True}}]
                }
            },
        )

        result = runner.invoke(app, ["pools", "show", "impossible", "-c", str(config)])

        assert result.exit_code == 1
        assert "free" in result.stderr
        assert "openai_compat" in result.stderr

    def test_an_unknown_pool_lists_the_known_ones(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        config = write_registry(
            tmp_path / "registry", catalog_server, {"cheap": ["pinned"]}
        )

        result = runner.invoke(app, ["pools", "show", "ghost", "-c", str(config)])

        assert result.exit_code == 1
        assert "ghost" in result.stderr and "cheap" in result.stderr


class TestPoolsCheck:
    def test_every_member_is_probed_and_reported(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        catalog_server.default = "OK"
        config = write_registry(
            tmp_path / "registry", catalog_server, {"cheap": ["pinned"]}
        )

        result = runner.invoke(app, ["pools", "check", "cheap", "-c", str(config)])

        assert result.exit_code == 0, result.stderr
        assert "alive" in result.stdout
        assert "1/1 member(s) answered" in result.stderr

    def test_a_dead_member_is_reported_and_the_probe_still_exits_zero(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        """A probe reports; it does not judge (FR-12)."""
        catalog_server.script = {"pinned": [(503, {"error": {"message": "down"}})]}
        catalog_server.default = "OK"
        config = write_registry(
            tmp_path / "registry", catalog_server, {"cheap": ["pinned"]}
        )

        result = runner.invoke(app, ["pools", "check", "cheap", "-c", str(config)])

        assert result.exit_code == 0, result.stderr
        assert "failed" in result.stdout
        assert "0/1 member(s) answered" in result.stderr

    def test_probing_writes_no_run_directory(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        catalog_server.default = "OK"
        config = write_registry(
            tmp_path / "registry", catalog_server, {"cheap": ["pinned"]}
        )

        runner.invoke(app, ["pools", "check", "cheap", "-c", str(config)])

        assert list_runs(tmp_path / ".runs") == []
        assert not (tmp_path / ".runs").exists()


class TestModelsList:
    def test_the_provider_catalog_is_listed(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        config = write_registry(tmp_path / "registry", catalog_server, {})

        result = runner.invoke(app, ["models", "list", "local", "-c", str(config)])

        assert result.exit_code == 0, result.stderr
        assert "free-fast" in result.stdout
        assert "3 of 3 model(s) shown" in result.stderr

    def test_the_limit_is_applied(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        config = write_registry(tmp_path / "registry", catalog_server, {})

        result = runner.invoke(
            app, ["models", "list", "local", "-c", str(config), "--limit", "1"]
        )

        assert "1 of 3 model(s) shown" in result.stderr

    def test_a_provider_with_no_catalog_is_refused(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        config = tmp_path / "registry"
        config.mkdir()
        (config / "providers.yaml").write_text(
            yaml.safe_dump({"bare": {"base_url": catalog_server.base_url}})
        )

        result = runner.invoke(app, ["models", "list", "bare", "-c", str(config)])

        assert result.exit_code == 1
        assert "declares no catalog" in result.stderr


class TestResolutionSnapshot:
    JOB: ClassVar[dict[str, Any]] = {
        "name": "discovered-run",
        "input": {"path": "posts.csv", "key": "post_id"},
        "fields": [
            {
                "name": "severity",
                "kind": "classify",
                "pool": "discovered",
                "vocabulary": ["high", "low"],
                "prompt": {"inputs": ["title"], "template": "{{ title }}"},
            }
        ],
    }

    def job_dir(self, tmp_path: Path) -> Path:
        directory = tmp_path / "job"
        directory.mkdir()
        (directory / "job.yaml").write_text(yaml.safe_dump(self.JOB, sort_keys=False))
        (directory / "posts.csv").write_text("post_id,title\n1,Leak\n2,Noise\n")
        return directory

    def config(self, tmp_path: Path, server: FakeModelServer) -> Path:
        return write_registry(
            tmp_path / "registry",
            server,
            {
                "discovered": {
                    "strategy": "waterfall",
                    "entries": [
                        {"discover": {"provider": "local", "include": ["free"]}}
                    ],
                }
            },
        )

    def test_a_discovery_pool_runs_and_records_what_it_resolved_to(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        catalog_server.default = "high"
        job_dir = self.job_dir(tmp_path)

        result = runner.invoke(
            app,
            ["run", str(job_dir), "-c", str(self.config(tmp_path, catalog_server))],
        )

        assert result.exit_code == 0, result.stderr
        run = list_runs(job_dir / ".runs", "discovered-run")[0]
        snapshot = json.loads((run / POOL_RESOLVED_FILENAME).read_text())
        assert snapshot[0]["pool"] == "discovered"
        assert [member["name"] for member in snapshot[0]["members"]] == [
            "free-fast",
            "free-slow",
        ]
        assert snapshot[0]["members"][0]["source"] == "discovered"

    def test_a_resume_reuses_the_snapshot_instead_of_re_querying(
        self, tmp_path: Path, catalog_server: FakeModelServer
    ) -> None:
        """Free-tier membership churns; a resumed run must not change models."""
        catalog_server.default = "not-a-label"
        job_dir = self.job_dir(tmp_path)
        config = self.config(tmp_path, catalog_server)

        first = runner.invoke(app, ["run", str(job_dir), "-c", str(config)])
        assert first.exit_code == 1  # nothing validated

        # The catalog now serves an entirely different free model.
        catalog_server.catalog_payload = {
            "data": [{"id": "free-newcomer", "pricing": {"prompt": "0"}}]
        }
        catalog_server.default = "high"
        catalog_server.requests.clear()

        second = runner.invoke(app, ["run", str(job_dir), "-c", str(config)])

        assert second.exit_code == 0, second.stderr
        answered = {request["model"] for request in catalog_server.requests}
        assert answered == {"free-fast"}
        assert "free-newcomer" not in answered
