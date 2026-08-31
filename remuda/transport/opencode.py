"""The opencode transport: `opencode run -m <model> <prompt>` (FR-3).

Laptop-only — it borrows the operator's own opencode session, so it cannot
run in CI or an executor. The parser is deliberately trivial: the whole of
stdout is the answer body. An opencode release that changes its framing must
be caught by the version probe, not compensated for by a clever parser.

SECURITY: the command is built as an argument list and run with shell=False
(`security/input-validation.md` — Shell Commands with User Input). Row data
reaches the process only as one argv element, never as shell text.
"""

import asyncio
import contextlib
import shutil
import time
from typing import Final

from remuda.registry.models import Provider
from remuda.transport.errors import (
    PermanentTransportError,
    TransientTransportError,
    TransportConfigurationError,
)
from remuda.transport.models import CompletionRequest, CompletionResult, Usage

DEFAULT_COMMAND: Final = "opencode"

#: Stderr fragments that mean "this will fail the same way next time".
_PERMANENT_SIGNATURES: Final[tuple[str, ...]] = (
    "unknown model",
    "not found",
    "unauthorized",
    "not authenticated",
    "no such model",
    "invalid model",
)


class OpencodeTransport:
    """Runs one completion per subprocess through the opencode CLI."""

    def __init__(
        self, provider: Provider, timeout_seconds: float | None = None
    ) -> None:
        self._provider = provider
        self._command = provider.command or DEFAULT_COMMAND
        self._timeout_seconds = timeout_seconds or provider.timeout_seconds

    @property
    def command(self) -> str:
        """The executable this transport invokes."""
        return self._command

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run one completion.

        Raises:
            TransportConfigurationError: the executable is not on PATH.
            TransientTransportError: the run timed out or failed in a way that
                may succeed next time.
            PermanentTransportError: the model is unknown or unauthorized.
        """
        argv = self._argv(request)
        started = time.monotonic()
        process = await self._spawn(argv)
        stdout, stderr = await self._communicate(process, request)
        latency_ms = (time.monotonic() - started) * 1000

        if process.returncode != 0:
            raise _classify(self._command, request.model_id, process.returncode, stderr)
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            raise TransientTransportError(
                f"'{self._command} run' produced no output for '{request.model_id}'"
            )
        return CompletionResult(
            text=text,
            model_id=request.model_id,
            usage=Usage(),  # the CLI reports no token accounting
            latency_ms=latency_ms,
        )

    async def aclose(self) -> None:
        """Nothing to release — each call is its own process."""

    async def probe_version(self) -> str:
        """Return the opencode version, for a run's preflight.

        Raises:
            TransportConfigurationError: the executable is missing.
            TransientTransportError: the probe failed or timed out.
        """
        process = await self._spawn([self._command, "--version"])
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        except TimeoutError as error:
            await _terminate(process)
            raise TransientTransportError(
                f"'{self._command} --version' did not answer within 15s"
            ) from error
        if process.returncode != 0:
            raise TransientTransportError(
                f"'{self._command} --version' exited {process.returncode}: "
                f"{_tail(stderr)}"
            )
        return stdout.decode("utf-8", errors="replace").strip()

    # -- internals ---------------------------------------------------------

    def _argv(self, request: CompletionRequest) -> list[str]:
        """Build the argument LIST. Never a shell string."""
        prompt = request.prompt
        if request.system:
            prompt = f"{request.system}\n\n{prompt}"
        return [self._command, "run", "-m", request.model_id, prompt]

    async def _spawn(self, argv: list[str]) -> asyncio.subprocess.Process:
        if shutil.which(argv[0]) is None:
            raise TransportConfigurationError(
                f"provider '{self._provider.name}' runs '{argv[0]}', which is "
                "not on PATH — install opencode, or point the provider's "
                "'command' at its executable"
            )
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise TransientTransportError(
                f"cannot start '{argv[0]}': {error}"
            ) from error

    async def _communicate(
        self, process: asyncio.subprocess.Process, request: CompletionRequest
    ) -> tuple[bytes, bytes]:
        timeout = request.timeout_seconds or self._timeout_seconds
        try:
            return await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as error:
            await _terminate(process)
            raise TransientTransportError(
                f"'{self._command} run' exceeded {timeout:.0f}s for "
                f"'{request.model_id}' and was killed"
            ) from error


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Stop a process that outstayed its timeout, without leaving it behind."""
    if process.returncode is not None:
        return
    process.kill()
    # A kill the OS refuses is not worth failing the run over.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)
    _close_transport(process)


def _close_transport(process: asyncio.subprocess.Process) -> None:
    """Close the subprocess transport of a process we killed.

    `asyncio.subprocess.Process` exposes no public close, and a transport
    left open after a kill is finalized later — on an event loop that may
    already be closed, which prints a stray traceback at the operator who
    only asked for a timeout. Closing it here keeps a timeout quiet.
    """
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    with contextlib.suppress(RuntimeError):
        transport.close()


def _classify(
    command: str, model_id: str, returncode: int | None, stderr: bytes
) -> Exception:
    detail = _tail(stderr)
    message = (
        f"'{command} run -m {model_id}' exited {returncode}: {detail or '(no output)'}"
    )
    lowered = detail.casefold()
    if any(signature in lowered for signature in _PERMANENT_SIGNATURES):
        return PermanentTransportError(message)
    return TransientTransportError(message)


def _tail(stderr: bytes, limit: int = 300) -> str:
    text = " ".join(stderr.decode("utf-8", errors="replace").split())
    return text[-limit:] if len(text) > limit else text
