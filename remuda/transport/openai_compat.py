"""The OpenAI-compatible transport — OpenRouter, Ollama, vLLM, NIM (FR-3).

One shape covers every provider that speaks `/chat/completions`. Per-model
quirks (`reasoning_effort`, arbitrary `extra_body`) are merged at the call
site rather than branched on by provider name.
"""

import os
import time
from collections.abc import Mapping
from typing import Any

import httpx

from remuda.registry.models import ModelConfig, Provider
from remuda.transport.errors import (
    PermanentTransportError,
    RateLimitedError,
    TransientTransportError,
    TransportConfigurationError,
    TransportError,
)
from remuda.transport.models import (
    CompletionRequest,
    CompletionResult,
    Transport,
    Usage,
)

#: Status codes that mean "try again later", not "this request is wrong".
_TRANSIENT_STATUS = frozenset({408, 409, 500, 502, 503, 504, 529})

_COMPLETIONS_PATH = "/chat/completions"


class OpenAICompatTransport:
    """Calls a model over an OpenAI-compatible `/chat/completions` endpoint."""

    def __init__(
        self,
        provider: Provider,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = provider
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=provider.timeout_seconds)

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Perform one completion.

        Raises:
            TransientTransportError: timeout, connection failure, 5xx, 429.
            PermanentTransportError: auth failure, bad request, unknown model.
        """
        started = time.monotonic()
        try:
            response = await self._client.post(
                self._url(),
                json=self._body(request),
                headers=self._headers(),
                timeout=request.timeout_seconds,
            )
        except httpx.TransportError as error:
            # httpx timeouts/connect errors do NOT subclass OSError —
            # observability.md §7 names this exact trap.
            raise TransientTransportError(
                f"{type(error).__name__} calling {self._provider.name}: {error}"
            ) from error

        _raise_for_status(response, self._provider.name, request.model_id)
        latency_ms = (time.monotonic() - started) * 1000
        return _parse(response, request.model_id, latency_ms)

    async def aclose(self) -> None:
        """Close the HTTP client when this transport owns it."""
        if self._owns_client:
            await self._client.aclose()

    def _url(self) -> str:
        base = (self._provider.base_url or "").rstrip("/")
        return f"{base}{_COMPLETIONS_PATH}"

    def _headers(self) -> dict[str, str]:
        headers = dict(self._provider.headers)
        headers.setdefault("Content-Type", "application/json")
        key_env = self._provider.key_env
        if key_env:
            key = os.environ.get(key_env)
            if not key:
                raise TransportConfigurationError(
                    f"provider '{self._provider.name}' reads its key from "
                    f"${key_env}, which is unset — export it, or point key_env "
                    "at the variable that holds the key"
                )
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _body(self, request: CompletionRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})

        body: dict[str, Any] = {"model": request.model_id, "messages": messages}
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        # Widest scope first: provider defaults, then per-model quirks, then
        # anything the caller set for this single request.
        body.update(self._provider.extra_body)
        body.update(request.extra_body)
        return body


def catalog_headers(provider: Provider) -> dict[str, str]:
    """Headers for a read-only catalog request against this provider.

    Unlike a completion, a missing key is not fatal here: many catalogs are
    readable anonymously, and refusing to list models because a key is unset
    would be unhelpful.
    """
    headers = dict(provider.headers)
    key = os.environ.get(provider.key_env or "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def build_request_extras(model: ModelConfig) -> dict[str, Any]:
    """Merge a model's declared quirks into a request body fragment."""
    extras: dict[str, Any] = dict(model.extra_body)
    if model.reasoning_effort is not None:
        extras["reasoning_effort"] = model.reasoning_effort
    return extras


def _raise_for_status(response: httpx.Response, provider: str, model_id: str) -> None:
    if response.status_code < 400:
        return
    detail = _error_detail(response)
    if response.status_code == 429:
        raise RateLimitedError(
            f"{provider} rate-limited '{model_id}': {detail}",
            retry_after_seconds=_retry_after(response),
        )
    if response.status_code in _TRANSIENT_STATUS:
        raise TransientTransportError(
            f"{provider} returned {response.status_code} for '{model_id}': {detail}"
        )
    raise PermanentTransportError(
        f"{provider} returned {response.status_code} for '{model_id}': {detail}"
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200].strip() or "(no body)"
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message", error))
        if error is not None:
            return str(error)
    return str(payload)[:200]


def _parse(
    response: httpx.Response, model_id: str, latency_ms: float
) -> CompletionResult:
    try:
        payload = response.json()
    except ValueError as error:
        raise TransientTransportError(
            f"'{model_id}' returned a non-JSON body: {response.text[:200]!r}"
        ) from error

    choices = payload.get("choices") if isinstance(payload, Mapping) else None
    if not choices:
        raise TransientTransportError(
            f"'{model_id}' returned no choices: {str(payload)[:200]}"
        )
    message = choices[0].get("message") or {}
    text = message.get("content")
    if text is None:
        raise TransientTransportError(f"'{model_id}' returned a choice with no content")
    return CompletionResult(
        text=str(text),
        model_id=model_id,
        usage=_usage(payload.get("usage")),
        latency_ms=latency_ms,
    )


def _usage(raw: Any) -> Usage:
    if not isinstance(raw, Mapping):
        return Usage()
    cost = raw.get("cost")
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
    )


def build_transport(provider: Provider) -> Transport:
    """Build the transport a provider's kind calls for.

    Raises:
        TransportError: the provider's kind has no transport.
    """
    if provider.kind == "openai_compat":
        return OpenAICompatTransport(provider)
    if provider.kind == "opencode":
        # Imported here: the opencode transport imports this module for its
        # error types, so a module-level import would be circular.
        from remuda.transport.opencode import OpencodeTransport  # noqa: PLC0415

        return OpencodeTransport(provider)
    raise TransportError(
        f"provider '{provider.name}' has kind '{provider.kind}', which has no transport"
    )
