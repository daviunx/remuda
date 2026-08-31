"""The opencode subprocess transport against a fake executable (FR-3).

A real subprocess, a real PATH lookup, real exit codes — the only way to
prove the argv contract and the timeout kill actually hold.
"""

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from remuda.catalog.resolve import ResolvedMember, ResolvedPool
from remuda.preflight import preflight_opencode
from remuda.registry.models import Provider
from remuda.registry.registry import Registry
from remuda.transport.errors import (
    PermanentTransportError,
    TransientTransportError,
    TransportConfigurationError,
)
from remuda.transport.models import CompletionRequest
from remuda.transport.opencode import OpencodeTransport

pytestmark = pytest.mark.integration

#: argv is [opencode, run, -m, <model>, <prompt>] — so $3 is the model and
#: $4 is the whole prompt, as ONE argument.
ECHO_PROMPT = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "opencode 0.4.2"; exit 0; fi
printf '%s' "$4"
"""

FIXED_ANSWER = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "opencode 0.4.2"; exit 0; fi
echo "  high  "
"""

UNKNOWN_MODEL = """#!/bin/sh
echo "error: unknown model $3" >&2
exit 2
"""

FLAKY = """#!/bin/sh
echo "error: connection reset by peer" >&2
exit 1
"""

SLOW = """#!/bin/sh
sleep 30
"""

SILENT = """#!/bin/sh
exit 0
"""


@pytest.fixture
def fake_opencode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Put a scripted `opencode` on PATH and return its directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    yield bin_dir


def install(bin_dir: Path, script: str, name: str = "opencode") -> Path:
    path = bin_dir / name
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def transport(**overrides: object) -> OpencodeTransport:
    provider = Provider.model_validate(
        {"name": "laptop", "kind": "opencode", **overrides}
    )
    return OpencodeTransport(provider)


def request(prompt: str = "Classify: Leak", **overrides: object) -> CompletionRequest:
    fields: dict[str, object] = {
        "model_id": "anthropic/claude-haiku-4",
        "prompt": prompt,
        "timeout_seconds": 20.0,
    }
    return CompletionRequest(**{**fields, **overrides})  # type: ignore[arg-type]


class TestAnswers:
    async def test_the_whole_stdout_is_the_answer(self, fake_opencode: Path) -> None:
        install(fake_opencode, FIXED_ANSWER)

        result = await transport().complete(request())

        assert result.text == "high"
        assert result.model_id == "anthropic/claude-haiku-4"
        assert result.latency_ms > 0

    async def test_the_prompt_is_passed_as_one_argument(
        self, fake_opencode: Path
    ) -> None:
        install(fake_opencode, ECHO_PROMPT)

        result = await transport().complete(request("Title: Leak\nBody: Water"))

        assert result.text == "Title: Leak\nBody: Water"

    async def test_a_system_prompt_leads_the_single_prompt_argument(
        self, fake_opencode: Path
    ) -> None:
        install(fake_opencode, ECHO_PROMPT)

        result = await transport().complete(
            request("Severity?", system="You are terse.")
        )

        assert result.text == "You are terse.\n\nSeverity?"

    async def test_an_empty_answer_is_transient(self, fake_opencode: Path) -> None:
        install(fake_opencode, SILENT)

        with pytest.raises(TransientTransportError, match="no output"):
            await transport().complete(request())

    async def test_the_version_probe_reads_the_cli(self, fake_opencode: Path) -> None:
        install(fake_opencode, FIXED_ANSWER)

        assert await transport().probe_version() == "opencode 0.4.2"


class TestArgvSafety:
    async def test_shell_metacharacters_in_a_prompt_are_inert(
        self, fake_opencode: Path, tmp_path: Path
    ) -> None:
        """The prompt is one argv element — never text a shell interprets."""
        install(fake_opencode, ECHO_PROMPT)
        marker = tmp_path / "pwned"
        hostile = (
            f"Classify this; touch {marker} && echo $(whoami) `id` | tee /dev/null"
        )

        result = await transport().complete(request(hostile))

        assert result.text == hostile  # passed through verbatim
        assert not marker.exists()  # nothing was executed

    async def test_a_provider_command_with_shell_syntax_is_refused(self) -> None:
        with pytest.raises(ValueError, match="bare executable"):
            Provider(name="laptop", kind="opencode", command="opencode; rm -rf /")

    async def test_the_command_is_only_meaningful_for_opencode(self) -> None:
        with pytest.raises(ValueError, match="only meaningful"):
            Provider(
                name="p",
                kind="openai_compat",
                base_url="http://x/v1",
                command="opencode",
            )


class TestFailures:
    async def test_an_unknown_model_is_permanent(self, fake_opencode: Path) -> None:
        install(fake_opencode, UNKNOWN_MODEL)

        with pytest.raises(PermanentTransportError, match="unknown model"):
            await transport().complete(request())

    async def test_a_generic_failure_is_transient(self, fake_opencode: Path) -> None:
        install(fake_opencode, FLAKY)

        with pytest.raises(TransientTransportError, match="connection reset"):
            await transport().complete(request())

    async def test_a_slow_run_is_killed_and_reported_transient(
        self, fake_opencode: Path
    ) -> None:
        install(fake_opencode, SLOW)

        with pytest.raises(TransientTransportError, match="was killed"):
            await transport().complete(request(timeout_seconds=0.5))

    async def test_a_missing_executable_names_the_remedy(self) -> None:
        with pytest.raises(TransportConfigurationError) as exc_info:
            await transport(command="remuda-no-such-binary").complete(request())

        assert "not on PATH" in str(exc_info.value)

    async def test_a_custom_command_name_is_honoured(self, fake_opencode: Path) -> None:
        install(fake_opencode, FIXED_ANSWER, name="opencode-nightly")

        result = await transport(command="opencode-nightly").complete(request())

        assert result.text == "high"


class TestRunPreflight:
    """A run that will use opencode probes its version before calling it."""

    async def test_the_probe_announces_the_version(self, fake_opencode: Path) -> None:
        install(fake_opencode, FIXED_ANSWER)
        registry = Registry(
            providers=[Provider(name="laptop", kind="opencode")],
        )
        resolved = ResolvedPool(
            pool="laptop-pool",
            members=[
                ResolvedMember(name="m", provider="laptop", model_id="anthropic/x")
            ],
        )

        announcements = await preflight_opencode(registry, [resolved])

        assert len(announcements) == 1
        assert "0.4.2" in announcements[0]
        assert "laptop-only" in announcements[0]

    async def test_a_run_using_no_opencode_provider_probes_nothing(self) -> None:
        registry = Registry(
            providers=[Provider(name="http", base_url="http://localhost:1/v1")]
        )
        resolved = ResolvedPool(
            pool="p",
            members=[ResolvedMember(name="m", provider="http", model_id="x")],
        )

        assert await preflight_opencode(registry, [resolved]) == []

    async def test_a_missing_opencode_is_reported_before_any_model_call(self) -> None:
        registry = Registry(
            providers=[
                Provider(
                    name="laptop", kind="opencode", command="remuda-no-such-binary"
                )
            ]
        )
        resolved = ResolvedPool(
            pool="p",
            members=[ResolvedMember(name="m", provider="laptop", model_id="x")],
        )

        with pytest.raises(TransportConfigurationError, match="not on PATH"):
            await preflight_opencode(registry, [resolved])
