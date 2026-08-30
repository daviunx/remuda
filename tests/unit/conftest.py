"""Fixtures and guards shared by the zero-network core's unit tests."""

import socket
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

JobDirFactory = Callable[..., Path]


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail loudly if anything under test opens a socket.

    Phase 1 is the zero-network core: `check`, `preview` and `init` must not
    reach a model endpoint. A guard is the only way to prove that.
    """

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the zero-network core opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    yield


@pytest.fixture
def write_job_dir(tmp_path: Path) -> JobDirFactory:
    """Write a job directory: the spec plus any extra files it points at."""

    def factory(
        spec: Mapping[str, Any],
        files: Mapping[str, str] | None = None,
        name: str = "job",
    ) -> Path:
        job_dir = tmp_path / name
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job.yaml").write_text(
            yaml.safe_dump(dict(spec), sort_keys=False), encoding="utf-8"
        )
        for relative, content in (files or {}).items():
            target = job_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return job_dir

    return factory


@pytest.fixture
def write_registry_dir(tmp_path: Path) -> Callable[..., Path]:
    """Write a registry configuration layer directory."""

    def factory(
        providers: Mapping[str, Any] | None = None,
        models: Mapping[str, Any] | None = None,
        pools: Mapping[str, Any] | None = None,
        name: str = "registry",
    ) -> Path:
        directory = tmp_path / name
        directory.mkdir(parents=True, exist_ok=True)
        for filename, payload in (
            ("providers.yaml", providers),
            ("models.yaml", models),
            ("pools.yaml", pools),
        ):
            if payload is not None:
                (directory / filename).write_text(
                    yaml.safe_dump(dict(payload), sort_keys=False), encoding="utf-8"
                )
        return directory

    return factory
