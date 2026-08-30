"""A scripted stand-in for a model endpoint.

This is a stub implementation of the `Transport` protocol, not a mock of the
code under test: it lets the ladder's decisions be driven by an exact
sequence of answers and failures, with no I/O of any kind.
"""

from collections.abc import Mapping, Sequence

from remuda.registry.models import ModelConfig, Provider
from remuda.transport.models import CompletionRequest, CompletionResult, Usage

#: One scripted step: text to return, or an exception to raise.
Step = str | BaseException


class ScriptedTransport:
    """Returns (or raises) the next scripted step for the model being called."""

    def __init__(
        self,
        script: Mapping[str, Sequence[Step]] | None = None,
        default: Step = "unscripted",
        usage: Usage | None = None,
    ) -> None:
        self._script = {model: list(steps) for model, steps in (script or {}).items()}
        self._default = default
        self._usage = usage or Usage(prompt_tokens=10, completion_tokens=4)
        self.requests: list[CompletionRequest] = []
        self.closed = False

    @property
    def prompts(self) -> list[str]:
        """Every prompt this transport was asked to complete, in order."""
        return [request.prompt for request in self.requests]

    def prompts_for(self, model_id: str) -> list[str]:
        """Prompts sent to one model id, in order."""
        return [
            request.prompt for request in self.requests if request.model_id == model_id
        ]

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Return the next scripted answer for this model.

        Raises:
            BaseException: whatever the script placed at this position.
        """
        self.requests.append(request)
        steps = self._script.get(request.model_id)
        step = steps.pop(0) if steps else self._default
        if isinstance(step, BaseException):
            raise step
        return CompletionResult(
            text=step,
            model_id=request.model_id,
            usage=self._usage,
            latency_ms=1.0,
        )

    async def aclose(self) -> None:
        """Record that the runner closed this transport."""
        self.closed = True


def model(name: str, provider: str = "fake", **overrides: object) -> ModelConfig:
    """A registry model whose model_id is its name — scripts key on that."""
    return ModelConfig.model_validate(
        {"name": name, "provider": provider, "model_id": name, **overrides}
    )


def provider(name: str = "fake") -> Provider:
    """A provider that needs no credentials."""
    return Provider(name=name, base_url="http://localhost:0/v1")
