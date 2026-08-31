"""The OpenAI-compatible transport against a live endpoint (FR-3, FR-4).

Status-code classification and body parsing are only proven over a real HTTP
call — a scripted transport asserts nothing about either.
"""

from typing import Any

import pytest

from remuda.registry.models import Provider
from remuda.transport.errors import (
    PermanentTransportError,
    RateLimitedError,
    TransientTransportError,
    TransportConfigurationError,
)
from remuda.transport.models import CompletionRequest, CompletionResult
from remuda.transport.openai_compat import OpenAICompatTransport
from tests.integration.conftest import FakeModelServer

pytestmark = pytest.mark.integration


def request(**overrides: Any) -> CompletionRequest:
    fields: dict[str, Any] = {
        "model_id": "a",
        "prompt": "Classify: Leak",
        "timeout_seconds": 5.0,
    }
    return CompletionRequest(**{**fields, **overrides})


async def complete(
    server: FakeModelServer, provider: Provider | None = None
) -> CompletionResult:
    transport = OpenAICompatTransport(
        provider or Provider(name="fake", base_url=server.base_url)
    )
    try:
        return await transport.complete(request())
    finally:
        await transport.aclose()


class TestSuccessfulCall:
    async def test_answer_and_usage_are_parsed(
        self, model_server: FakeModelServer
    ) -> None:
        model_server.default = "high"

        result = await complete(model_server)

        assert result.text == "high"
        assert result.usage.prompt_tokens == 11
        assert result.usage.cost_usd == 0.0001
        assert result.latency_ms > 0

    async def test_the_prompt_is_sent_as_a_user_message(
        self, model_server: FakeModelServer
    ) -> None:
        await complete(model_server)

        sent = model_server.requests[0]
        assert sent["model"] == "a"
        assert sent["messages"][-1] == {
            "role": "user",
            "content": "Classify: Leak",
        }

    async def test_a_system_prompt_leads_the_messages(
        self, model_server: FakeModelServer
    ) -> None:
        transport = OpenAICompatTransport(
            Provider(name="fake", base_url=model_server.base_url)
        )
        await transport.complete(request(system="You are terse."))
        await transport.aclose()

        assert model_server.requests[0]["messages"][0] == {
            "role": "system",
            "content": "You are terse.",
        }

    async def test_provider_and_request_extras_are_merged_into_the_body(
        self, model_server: FakeModelServer
    ) -> None:
        provider = Provider(
            name="fake",
            base_url=model_server.base_url,
            extra_body={"provider_flag": True, "shared": "provider"},
        )
        transport = OpenAICompatTransport(provider)
        await transport.complete(
            request(extra_body={"reasoning_effort": "low", "shared": "request"})
        )
        await transport.aclose()

        sent = model_server.requests[0]
        assert sent["provider_flag"] is True
        assert sent["reasoning_effort"] == "low"
        assert sent["shared"] == "request"  # the narrower scope wins


class TestCredentials:
    async def test_the_key_is_read_from_the_named_environment_variable(
        self, model_server: FakeModelServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REMUDA_TEST_KEY", "sk-from-env")
        provider = Provider(
            name="fake", base_url=model_server.base_url, key_env="REMUDA_TEST_KEY"
        )

        await complete(model_server, provider)

        assert model_server.headers_seen[0]["Authorization"] == "Bearer sk-from-env"

    async def test_an_unset_key_env_is_refused_before_the_call(
        self, model_server: FakeModelServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REMUDA_TEST_KEY", raising=False)
        provider = Provider(
            name="fake", base_url=model_server.base_url, key_env="REMUDA_TEST_KEY"
        )

        with pytest.raises(TransportConfigurationError) as exc_info:
            await complete(model_server, provider)

        assert "REMUDA_TEST_KEY" in str(exc_info.value)
        assert model_server.requests == []

    async def test_no_authorization_header_when_no_key_env_is_declared(
        self, model_server: FakeModelServer
    ) -> None:
        await complete(model_server)

        assert "Authorization" not in model_server.headers_seen[0]


class TestFailureClassification:
    @pytest.mark.parametrize("status", [500, 502, 503, 504, 408])
    async def test_server_errors_are_transient(
        self, model_server: FakeModelServer, status: int
    ) -> None:
        model_server.default = (status, {"error": {"message": "upstream sad"}})

        with pytest.raises(TransientTransportError, match="upstream sad"):
            await complete(model_server)

    async def test_rate_limit_carries_the_retry_after(
        self, model_server: FakeModelServer
    ) -> None:
        model_server.default = (429, {"error": {"message": "slow down"}})

        with pytest.raises(RateLimitedError) as exc_info:
            await complete(model_server)

        assert exc_info.value.retry_after_seconds == 7

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_client_errors_are_permanent(
        self, model_server: FakeModelServer, status: int
    ) -> None:
        model_server.default = (status, {"error": {"message": "unknown model"}})

        with pytest.raises(PermanentTransportError, match="unknown model"):
            await complete(model_server)

    async def test_a_body_with_no_choices_is_transient(
        self, model_server: FakeModelServer
    ) -> None:
        model_server.default = (200, {"choices": []})

        with pytest.raises(TransientTransportError, match="no choices"):
            await complete(model_server)

    async def test_an_unreachable_endpoint_is_transient(self) -> None:
        provider = Provider(name="dead", base_url="http://127.0.0.1:1/v1")
        transport = OpenAICompatTransport(provider)

        with pytest.raises(TransientTransportError):
            await transport.complete(request())
        await transport.aclose()
