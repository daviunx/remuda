"""FR-4 — the retry / rotate / mop-up ladder, driven by forced sequences.

Every model answer here is scripted, so each test pins one transition of the
state machine rather than a plausible end-to-end outcome.
"""

from typing import Any

import pytest

from remuda.engine.ladder import ChunkItem, run_chunk
from remuda.engine.lanes import ModelLane
from remuda.spec.models import FieldSpec
from remuda.transport.errors import (
    PermanentTransportError,
    RateLimitedError,
    TransientTransportError,
)
from remuda.transport.models import Usage
from tests.fakes.transport import ScriptedTransport, model

FIELD = FieldSpec.model_validate(
    {
        "name": "severity",
        "kind": "classify",
        "pool": "p",
        "vocabulary": ["high", "low"],
        "prompt": {"inputs": ["text"], "template": "{{ text }}"},
    }
)


def items(*keys: str) -> list[ChunkItem]:
    return [
        ChunkItem(key=key, row={"text": key}, prompt=f"prompt {key}") for key in keys
    ]


def lanes(transport: ScriptedTransport, *names: str, **kwargs: Any) -> list[ModelLane]:
    return [
        ModelLane(model=model(name, **kwargs), transport=transport) for name in names
    ]


def outcome_for(result: Any, key: str) -> Any:
    return next(outcome for outcome in result.outcomes if outcome.key == key)


class TestHappyPath:
    async def test_first_model_answering_validly_ends_the_ladder(self) -> None:
        transport = ScriptedTransport({"a": ["high"]})
        ladder = lanes(transport, "a", "b")

        result = await run_chunk(FIELD, items("1"), ladder)

        decided = outcome_for(result, "1")
        assert decided.outcome == "ok"
        assert decided.value == "high"
        assert decided.model == "a"
        assert decided.attempts == 1
        assert transport.prompts_for("b") == []

    async def test_answer_order_follows_the_input_order(self) -> None:
        transport = ScriptedTransport({"a": ["2: low\n1: high"]})

        result = await run_chunk(FIELD, items("1", "2"), lanes(transport, "a"))

        assert [outcome.key for outcome in result.outcomes] == ["1", "2"]
        assert outcome_for(result, "1").value == "high"


class TestRetryThenRotate:
    async def test_invalid_answer_is_retried_on_the_same_model_first(self) -> None:
        transport = ScriptedTransport({"a": ["urgent", "high"]})

        result = await run_chunk(
            FIELD, items("1"), lanes(transport, "a", "b"), attempts_per_model=2
        )

        decided = outcome_for(result, "1")
        assert decided.outcome == "ok"
        assert decided.model == "a"
        assert decided.attempts == 2
        assert decided.rejects and "urgent" in decided.rejects[0]
        assert transport.prompts_for("b") == []

    async def test_retry_carries_the_repair_feedback(self) -> None:
        transport = ScriptedTransport({"a": ["urgent", "high"]})

        await run_chunk(FIELD, items("1"), lanes(transport, "a"), attempts_per_model=2)

        second_ask = transport.prompts_for("a")[1]
        assert "previous answer was rejected" in second_ask
        assert "urgent" in second_ask

    async def test_exhausted_attempts_rotate_to_the_next_model(self) -> None:
        transport = ScriptedTransport({"a": ["no", "nope"], "b": ["low"]})

        result = await run_chunk(
            FIELD, items("1"), lanes(transport, "a", "b"), attempts_per_model=2
        )

        decided = outcome_for(result, "1")
        assert decided.outcome == "ok"
        assert decided.model == "b"
        assert decided.attempts == 3
        assert len(transport.prompts_for("a")) == 2

    async def test_mop_up_answers_after_every_pool_model_failed(self) -> None:
        transport = ScriptedTransport(
            {"a": ["no"], "b": ["no"], "paid": ["high"]},
        )

        result = await run_chunk(
            FIELD, items("1"), lanes(transport, "a", "b", "paid"), attempts_per_model=1
        )

        assert outcome_for(result, "1").model == "paid"

    async def test_exhausting_the_ladder_fails_the_key_with_its_last_reason(
        self,
    ) -> None:
        transport = ScriptedTransport(default="not-a-label")

        result = await run_chunk(
            FIELD, items("1"), lanes(transport, "a", "b"), attempts_per_model=1
        )

        decided = outcome_for(result, "1")
        assert decided.outcome == "failed"
        assert decided.attempts == 2
        assert decided.reason is not None
        assert "not-a-label" in decided.reason
        assert len(decided.rejects) == 2


class TestBanking:
    async def test_valid_answers_survive_a_partially_invalid_packed_call(self) -> None:
        transport = ScriptedTransport(
            {"a": ["1: high\n2: nonsense\n3: low", "2: high"]}
        )

        result = await run_chunk(
            FIELD, items("1", "2", "3"), lanes(transport, "a"), attempts_per_model=2
        )

        assert [outcome.outcome for outcome in result.outcomes] == ["ok", "ok", "ok"]
        assert outcome_for(result, "2").attempts == 2
        assert outcome_for(result, "1").attempts == 1

    async def test_only_the_invalid_key_is_re_asked(self) -> None:
        transport = ScriptedTransport(
            {"a": ["1: high\n2: nonsense\n3: low", "2: high"]}
        )

        await run_chunk(
            FIELD, items("1", "2", "3"), lanes(transport, "a"), attempts_per_model=2
        )

        second_ask = transport.prompts_for("a")[1]
        assert "prompt 2" in second_ask
        assert "prompt 1" not in second_ask and "prompt 3" not in second_ask

    async def test_an_unanswered_key_re_enters_with_feedback(self) -> None:
        transport = ScriptedTransport({"a": ["1: high", "2: low"]})

        result = await run_chunk(
            FIELD, items("1", "2"), lanes(transport, "a"), attempts_per_model=2
        )

        assert outcome_for(result, "2").outcome == "ok"
        assert "did not answer" in transport.prompts_for("a")[1]

    async def test_banked_answers_are_not_re_asked_on_the_next_model(self) -> None:
        transport = ScriptedTransport({"a": ["1: high\n2: junk"], "b": ["low"]})

        result = await run_chunk(
            FIELD, items("1", "2"), lanes(transport, "a", "b"), attempts_per_model=1
        )

        assert outcome_for(result, "1").model == "a"
        assert outcome_for(result, "2").model == "b"
        assert "prompt 1" not in transport.prompts_for("b")[0]


class TestTransportFailures:
    async def test_transient_failure_cools_the_model_and_rotates(self) -> None:
        transport = ScriptedTransport(
            {"a": [TransientTransportError("connection refused")], "b": ["high"]}
        )
        ladder = lanes(transport, "a", "b")

        result = await run_chunk(FIELD, items("1"), ladder, attempts_per_model=2)

        assert outcome_for(result, "1").model == "b"
        assert ladder[0].is_cooling
        assert ladder[0].stats.transient_failures == 1
        assert any("cooling down" in note for note in result.notes)

    async def test_transient_failure_consumes_no_validation_attempt(self) -> None:
        """The model is cooled, not spent: the key's attempt count is untouched."""
        transport = ScriptedTransport(
            {"a": [TransientTransportError("timeout")], "b": ["high"]}
        )

        result = await run_chunk(
            FIELD, items("1"), lanes(transport, "a", "b"), attempts_per_model=2
        )

        assert outcome_for(result, "1").attempts == 1
        assert len(transport.prompts_for("a")) == 1

    async def test_rate_limit_uses_the_provider_retry_after(self) -> None:
        transport = ScriptedTransport(
            {"a": [RateLimitedError("slow down", retry_after_seconds=30)], "b": ["low"]}
        )
        ladder = lanes(transport, "a", "b")

        result = await run_chunk(FIELD, items("1"), ladder)

        assert outcome_for(result, "1").outcome == "ok"
        assert any("30s" in note for note in result.notes)

    async def test_permanent_failure_removes_the_model_from_the_run(self) -> None:
        transport = ScriptedTransport(
            {"a": [PermanentTransportError("401 unauthorized")], "b": ["high"]}
        )
        ladder = lanes(transport, "a", "b")

        result = await run_chunk(FIELD, items("1"), ladder)

        assert outcome_for(result, "1").model == "b"
        assert ladder[0].disabled_reason is not None
        assert "401" in ladder[0].disabled_reason
        assert ladder[0].stats.permanent_failures == 1

    async def test_a_cooling_model_is_skipped_with_a_note(self) -> None:
        transport = ScriptedTransport({"b": ["high"]})
        ladder = lanes(transport, "a", "b")
        ladder[0].cool_down(60)

        result = await run_chunk(FIELD, items("1"), ladder)

        assert outcome_for(result, "1").model == "b"
        assert transport.prompts_for("a") == []
        assert any("skipped" in note for note in result.notes)

    async def test_every_model_failing_transiently_fails_the_key(self) -> None:
        transport = ScriptedTransport(default=TransientTransportError("unreachable"))

        result = await run_chunk(FIELD, items("1"), lanes(transport, "a", "b"))

        decided = outcome_for(result, "1")
        assert decided.outcome == "failed"
        assert decided.reason is not None
        assert "unreachable" in decided.reason


class TestLaneAccounting:
    async def test_answers_and_rejects_are_counted_per_model(self) -> None:
        transport = ScriptedTransport({"a": ["1: high\n2: junk"], "b": ["low"]})
        ladder = lanes(transport, "a", "b")

        await run_chunk(FIELD, items("1", "2"), ladder, attempts_per_model=1)

        assert ladder[0].stats.answers == 1
        assert ladder[0].stats.rejects == 1
        assert ladder[1].stats.answers == 1

    async def test_spend_ceiling_removes_the_model_not_the_run(self) -> None:
        transport = ScriptedTransport(
            {"a": ["nope", "nope"], "b": ["high"]},
            usage=Usage(cost_usd=1.0),
        )
        ladder = [
            ModelLane(model=model("a", max_cost_usd=0.5), transport=transport),
            ModelLane(model=model("b"), transport=transport),
        ]

        result = await run_chunk(FIELD, items("1"), ladder, attempts_per_model=2)

        assert outcome_for(result, "1").outcome == "ok"
        assert ladder[0].disabled_reason is not None
        assert "spend ceiling" in ladder[0].disabled_reason

    async def test_concurrency_is_bounded_per_model(self) -> None:
        lane = ModelLane(model=model("a", concurrency=1), transport=ScriptedTransport())

        assert lane.model.concurrency == 1
        assert lane.is_available


@pytest.mark.parametrize("attempts", [1, 3])
async def test_attempts_per_model_is_honoured(attempts: int) -> None:
    transport = ScriptedTransport(default="not-a-label")
    ladder = lanes(transport, "a")

    await run_chunk(FIELD, items("1"), ladder, attempts_per_model=attempts)

    assert len(transport.prompts_for("a")) == attempts
