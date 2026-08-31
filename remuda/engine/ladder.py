"""The retry / rotate / mop-up ladder (FR-4).

For one packed chunk: retry on the same model with repair feedback, rotate to
the next model in the pool, then the mop-up model, then record the key as
failed with its last reason. Valid answers are banked as soon as they arrive —
only the keys still invalid re-enter the ladder.

Transport failures never consume a validation attempt: they cool the model
down and the chunk moves on to the next one.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from remuda.engine.lanes import LaneUnavailableError, ModelLane
from remuda.engine.packing import pack_prompt, parse_answers, single_prompt_with_repair
from remuda.spec.models import FieldSpec
from remuda.transport.errors import (
    PermanentTransportError,
    RateLimitedError,
    TransientTransportError,
)
from remuda.validate.answers import validate_answer
from remuda.validate.verdict import Outcome

_NO_ANSWER = "You did not answer this item. Answer every item you are given."


@dataclass(frozen=True)
class ChunkItem:
    """One row's worth of work inside a packed call."""

    key: str
    row: Mapping[str, Any]
    prompt: str


@dataclass
class KeyOutcome:
    """What the ladder decided for one key."""

    key: str
    outcome: Outcome
    value: Any = None
    model: str | None = None
    attempts: int = 0
    rejects: tuple[str, ...] = ()
    reason: str | None = None
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class ChunkResult:
    """Every key's outcome, plus anything worth telling the operator."""

    outcomes: list[KeyOutcome] = dataclass_field(default_factory=list)
    notes: list[str] = dataclass_field(default_factory=list)
    models_used: list[str] = dataclass_field(default_factory=list)


@dataclass
class _Pending:
    """Mutable per-key state while the key is still walking the ladder."""

    item: ChunkItem
    attempts: int = 0
    rejects: list[str] = dataclass_field(default_factory=list)
    repair: str | None = None
    reason: str | None = None


async def run_chunk(
    field: FieldSpec,
    items: Sequence[ChunkItem],
    lanes: Sequence[ModelLane],
    attempts_per_model: int = 2,
) -> ChunkResult:
    """Walk one chunk down the ladder until every key is decided."""
    pending: dict[str, _Pending] = {item.key: _Pending(item=item) for item in items}
    result = ChunkResult()
    system = field.prompt.system if field.prompt is not None else None

    for lane in lanes:
        if not pending:
            break
        if not lane.is_available:
            result.notes.append(_unavailable_note(lane))
            continue
        await _walk_lane(
            field=field,
            lane=lane,
            pending=pending,
            attempts_per_model=attempts_per_model,
            system=system,
            result=result,
        )

    result.outcomes.extend(_fail(state) for state in pending.values())
    result.outcomes.sort(
        key=lambda outcome: [item.key for item in items].index(outcome.key)
    )
    return result


async def _walk_lane(
    *,
    field: FieldSpec,
    lane: ModelLane,
    pending: dict[str, _Pending],
    attempts_per_model: int,
    system: str | None,
    result: ChunkResult,
) -> None:
    """Spend this lane's attempts on whatever is still pending."""
    for _ in range(attempts_per_model):
        if not pending:
            return
        try:
            completion = await lane.call(_prompt_for(pending), system)
        except PermanentTransportError as error:
            lane.stats.permanent_failures += 1
            lane.disable(str(error))
            _mark_reason(pending, str(error))
            result.notes.append(f"model '{lane.name}' left the run: {error}")
            return
        except (TransientTransportError, LaneUnavailableError) as error:
            retry_after = getattr(error, "retry_after_seconds", None)
            delay = lane.cool_down(
                retry_after if isinstance(error, RateLimitedError) else None
            )
            _mark_reason(pending, str(error))
            result.notes.append(
                f"model '{lane.name}' cooling down for {delay:.0f}s: {error}"
            )
            return  # transient: rotate WITHOUT consuming a validation attempt

        if lane.name not in result.models_used:
            result.models_used.append(lane.name)
        _judge(field, lane, pending, completion, result)


def _judge(
    field: FieldSpec,
    lane: ModelLane,
    pending: dict[str, _Pending],
    completion: Any,
    result: ChunkResult,
) -> None:
    """Bank every valid answer; leave the rest pending with repair feedback."""
    keys = list(pending)
    answers = parse_answers(completion.text, keys)
    share = _usage_share(completion, len(keys))
    banked = 0
    rejected = 0

    for key in keys:
        state = pending[key]
        state.attempts += 1
        raw = answers.get(key)
        if raw is None:
            state.repair = _NO_ANSWER
            state.rejects.append(_NO_ANSWER)
            state.reason = _NO_ANSWER
            rejected += 1
            continue
        verdict = validate_answer(field, raw)
        if not verdict.is_valid:
            repair = verdict.repair or "the answer was rejected"
            state.repair = repair
            state.rejects.append(repair)
            state.reason = repair
            rejected += 1
            continue
        result.outcomes.append(
            KeyOutcome(
                key=key,
                outcome="ok",
                value=verdict.value,
                model=lane.name,
                attempts=state.attempts,
                rejects=tuple(state.rejects),
                latency_ms=completion.latency_ms,
                prompt_tokens=share[0],
                completion_tokens=share[1],
                cost_usd=share[2],
            )
        )
        del pending[key]
        banked += 1

    lane.record_answers(banked)
    lane.record_reject(rejected)


def _prompt_for(pending: Mapping[str, _Pending]) -> str:
    prompts = {key: state.item.prompt for key, state in pending.items()}
    repairs = {
        key: state.repair for key, state in pending.items() if state.repair is not None
    }
    if len(prompts) == 1:
        only = next(iter(pending))
        return single_prompt_with_repair(prompts[only], repairs.get(only))
    return pack_prompt(prompts, repairs)


def _usage_share(completion: Any, keys: int) -> tuple[int, int, float | None]:
    """Split a packed call's usage evenly across the keys it carried."""
    divisor = max(1, keys)
    usage = completion.usage
    cost = None if usage.cost_usd is None else usage.cost_usd / divisor
    return (
        usage.prompt_tokens // divisor,
        usage.completion_tokens // divisor,
        cost,
    )


def _mark_reason(pending: Mapping[str, _Pending], reason: str) -> None:
    for state in pending.values():
        state.reason = reason


def _fail(state: _Pending) -> KeyOutcome:
    return KeyOutcome(
        key=state.item.key,
        outcome="failed",
        attempts=state.attempts,
        rejects=tuple(state.rejects),
        reason=state.reason or "the pool produced no valid answer",
    )


def _unavailable_note(lane: ModelLane) -> str:
    if lane.disabled_reason is not None:
        return f"model '{lane.name}' skipped: {lane.disabled_reason}"
    return f"model '{lane.name}' skipped: cooling down"
