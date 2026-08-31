"""Done-When #2 — the rally severity backfill, reproduced through remuda.

The original was a one-off script (preserved at
`planning/neo-cli/neo-infer-cheap-bulk-llm/reference-driver-classify_parallel.py`):
649 radar catches, chunks of 25 scattered across a rotation of free models,
`act|plan|watch`, complete or nothing.

This reproduces that shape at the same row count through BOTH transports and
asserts the key column is byte-identical between them.

HARNESSED, NOT LIVE. The OpenAI-compatible leg runs against the loopback
server in tests/integration/conftest.py; the opencode leg runs a stub
executable on PATH. Neither leg proves anything about the availability or
answer quality of real free models — only that remuda's machinery reproduces
the driver's contract end to end.
"""

import csv
import io
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from remuda.cli.app import app
from remuda.ledger.store import RUNS_DIRNAME, list_runs
from tests.integration.conftest import FakeModelServer

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The driver's row count, chunk size, worker count and vocabulary.
ROWS = 649
CHUNK = 25
WORKERS = 5
VOCABULARY = ("act", "plan", "watch")

RUBRIC = (
    "You classify Short-Term Rental industry radar catches for property "
    "managers.\nAssign EXACTLY ONE severity per item: act, plan or watch.\n\n"
    "Title: {{ title }}\nImpact: {{ impact }}"
)


#: Deterministic answer per row, so both transports must agree exactly.
def expected_severity(index: int) -> str:
    return VOCABULARY[index % len(VOCABULARY)]


def slug(index: int) -> str:
    return f"catch-{index:04d}"


@pytest.fixture
def catches(tmp_path: Path) -> Path:
    """649 radar catches, the same shape the driver consumed."""
    path = tmp_path / "classify_input.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["slug", "title", "impact", "severity"])
        for index in range(ROWS):
            writer.writerow(
                [
                    slug(index),
                    f"Radar catch {index}",
                    f"PM impact statement {index}",
                    "",
                ]
            )
    return path


def job_directory(tmp_path: Path, catches: Path, pool: str, name: str) -> Path:
    """The driver's contract as a remuda job directory."""
    directory = tmp_path / f"job-{name}"
    directory.mkdir()
    (directory / "classify_input.csv").write_text(
        catches.read_text(encoding="utf-8"), encoding="utf-8"
    )
    job: dict[str, Any] = {
        "name": f"rally-severity-{name}",
        "input": {"path": "classify_input.csv", "key": "slug"},
        "execution": {
            "records_per_call": CHUNK,
            "workers": WORKERS,
            "attempts_per_model": 2,
        },
        "fields": [
            {
                "name": "severity",
                "kind": "classify",
                "pool": pool,
                "vocabulary": list(VOCABULARY),
                "prompt": {"inputs": ["title", "impact"], "template": RUBRIC},
            }
        ],
    }
    (directory / "job.yaml").write_text(yaml.safe_dump(job, sort_keys=False))
    return directory


def packed_answer(prompt: str) -> str:
    """Answer every slug the packed prompt carries, keyed, as a model would."""
    lines = []
    for index in range(ROWS):
        identifier = slug(index)
        if f"### item {identifier}" in prompt or prompt.strip().endswith(identifier):
            lines.append(f"{identifier}: {expected_severity(index)}")
    return "\n".join(lines)


@pytest.fixture
def http_registry(tmp_path: Path, model_server: FakeModelServer) -> Path:
    """A pool of four free models plus a paid mop-up, over HTTP."""
    directory = tmp_path / "registry-http"
    directory.mkdir()
    (directory / "providers.yaml").write_text(
        yaml.safe_dump({"openrouter": {"base_url": model_server.base_url}})
    )
    (directory / "models.yaml").write_text(
        yaml.safe_dump(
            {
                name: {"provider": "openrouter", "model_id": name}
                for name in ("glm", "nemotron", "gemma", "nano", "deepseek")
            }
        )
    )
    (directory / "pools.yaml").write_text(
        yaml.safe_dump(
            {
                "free-rotation": {
                    "strategy": "scatter",
                    "models": ["glm", "nemotron", "gemma", "nano"],
                    "mopup": "deepseek",
                }
            }
        )
    )
    return directory


@pytest.fixture
def opencode_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The same pool shape, answered by a stub `opencode` on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "opencode"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if sys.argv[1:2] == ['--version']:\n"
        "    print('opencode 0.4.2')\n"
        "    sys.exit(0)\n"
        "prompt = sys.argv[4]\n"
        "vocab = ['act', 'plan', 'watch']\n"
        "out = []\n"
        "for line in prompt.split('\\n'):\n"
        "    if line.startswith('### item '):\n"
        "        identifier = line[len('### item '):].strip()\n"
        "        index = int(identifier.split('-')[1])\n"
        "        out.append(f'{identifier}: {vocab[index % 3]}')\n"
        "print('\\n'.join(out))\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)

    directory = tmp_path / "registry-opencode"
    directory.mkdir()
    (directory / "providers.yaml").write_text(
        yaml.safe_dump({"laptop": {"kind": "opencode"}})
    )
    (directory / "models.yaml").write_text(
        yaml.safe_dump(
            {
                name: {"provider": "laptop", "model_id": f"openrouter/{name}:free"}
                for name in ("glm", "nemotron")
            }
        )
    )
    (directory / "pools.yaml").write_text(
        yaml.safe_dump(
            {"free-rotation": {"strategy": "scatter", "models": ["glm", "nemotron"]}}
        )
    )
    return directory


def severities(rendered: str) -> list[tuple[str, str]]:
    reader = csv.DictReader(io.StringIO(rendered))
    return [(row["slug"], row["severity"]) for row in reader]


def run_and_render(
    job_dir: Path, config_dir: Path
) -> tuple[Any, list[tuple[str, str]]]:
    result = runner.invoke(app, ["run", str(job_dir), "-c", str(config_dir)])
    if result.exit_code != 0:
        return result, []
    run_dir = list_runs(job_dir / RUNS_DIRNAME)[0]
    rendered = runner.invoke(app, ["render", str(run_dir), "--enrich"])
    assert rendered.exit_code == 0, rendered.stderr
    return result, severities(rendered.stdout)


class TestRallyReproduction:
    def test_the_openai_compatible_leg_classifies_every_row(
        self,
        tmp_path: Path,
        catches: Path,
        http_registry: Path,
        model_server: FakeModelServer,
    ) -> None:
        model_server.answer_fn = packed_answer
        job_dir = job_directory(tmp_path, catches, "free-rotation", "http")

        result, rows = run_and_render(job_dir, http_registry)

        assert result.exit_code == 0, result.stderr
        assert len(rows) == ROWS
        assert all(severity in VOCABULARY for _, severity in rows)
        assert rows[0] == (slug(0), expected_severity(0))
        assert rows[-1] == (slug(ROWS - 1), expected_severity(ROWS - 1))

    def test_the_rotation_actually_spread_across_the_pool(
        self,
        tmp_path: Path,
        catches: Path,
        http_registry: Path,
        model_server: FakeModelServer,
    ) -> None:
        """Scatter must use the whole pool, as the driver's rotation did."""
        model_server.answer_fn = packed_answer
        job_dir = job_directory(tmp_path, catches, "free-rotation", "spread")

        run_and_render(job_dir, http_registry)

        used = {request.get("model") for request in model_server.requests}
        assert used == {"glm", "nemotron", "gemma", "nano"}

    def test_packing_holds_the_drivers_chunk_size(
        self,
        tmp_path: Path,
        catches: Path,
        http_registry: Path,
        model_server: FakeModelServer,
    ) -> None:
        model_server.answer_fn = packed_answer
        job_dir = job_directory(tmp_path, catches, "free-rotation", "packed")

        run_and_render(job_dir, http_registry)

        completions = [r for r in model_server.requests if r.get("messages")]
        assert len(completions) == -(-ROWS // CHUNK) == 26

    def test_the_opencode_leg_classifies_every_row(
        self, tmp_path: Path, catches: Path, opencode_registry: Path
    ) -> None:
        job_dir = job_directory(tmp_path, catches, "free-rotation", "opencode")

        result, rows = run_and_render(job_dir, opencode_registry)

        assert result.exit_code == 0, result.stderr
        assert len(rows) == ROWS
        assert "laptop-only" in result.stderr  # the preflight probe announced itself

    def test_both_transports_agree_byte_for_byte_on_the_key_column(
        self,
        tmp_path: Path,
        catches: Path,
        http_registry: Path,
        opencode_registry: Path,
        model_server: FakeModelServer,
    ) -> None:
        """The task's acceptance: same keys, same labels, either transport."""
        model_server.answer_fn = packed_answer

        _, over_http = run_and_render(
            job_directory(tmp_path, catches, "free-rotation", "cmp-http"),
            http_registry,
        )
        _, over_opencode = run_and_render(
            job_directory(tmp_path, catches, "free-rotation", "cmp-opencode"),
            opencode_registry,
        )

        assert len(over_http) == ROWS
        assert over_http == over_opencode

    def test_a_run_that_cannot_complete_every_row_exits_non_zero(
        self,
        tmp_path: Path,
        catches: Path,
        http_registry: Path,
        model_server: FakeModelServer,
    ) -> None:
        """The driver's contract: complete, or unsuccessful. Never partial."""

        def all_but_one(prompt: str) -> str:
            answered = packed_answer(prompt)
            lines = [
                line
                for line in answered.split("\n")
                if not line.startswith(f"{slug(7)}:")
            ]
            return "\n".join(lines)

        model_server.answer_fn = all_but_one
        job_dir = job_directory(tmp_path, catches, "free-rotation", "incomplete")

        result = runner.invoke(app, ["run", str(job_dir), "-c", str(http_registry)])

        assert result.exit_code == 1
        assert slug(7) in result.stderr
