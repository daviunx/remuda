"""Packing several rows into one call, and unpacking the keyed answers (FR-4).

Packing is what makes a cheap model cheap: `records_per_call` rows ride in one
completion. The answers must therefore come back KEYED, so a partially valid
answer can be banked per key instead of thrown away wholesale.
"""

import json
import re
from collections.abc import Mapping, Sequence

#: One answer per line: `<key>: <answer>`, tolerant of list/bullet framing.
_KEYED_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?P<key>[^:\s][^:]{0,120}?)\s*[:=]\s*(?P<answer>.*)$"
)

_ITEM_HEADER = "### item "


def pack_prompt(
    prompts: Mapping[str, str],
    repairs: Mapping[str, str] | None = None,
) -> str:
    """Build one prompt covering several keyed items.

    A single item is sent unwrapped — asking a cheap model for a keyed line
    when there is only one answer costs accuracy for nothing.
    """
    if len(prompts) == 1 and not repairs:
        return next(iter(prompts.values()))

    keys = ", ".join(prompts)
    blocks = [
        "Answer every item below.",
        f"Reply with one line per item in the form `<id>: <answer>`, using "
        f"exactly these ids: {keys}. Write nothing else.",
    ]
    for key, prompt in prompts.items():
        block = f"{_ITEM_HEADER}{key}\n{prompt}"
        correction = (repairs or {}).get(key)
        if correction:
            block += f"\n\nYour previous answer was rejected: {correction}"
        blocks.append(block)
    return "\n\n".join(blocks)


def single_prompt_with_repair(prompt: str, repair: str | None) -> str:
    """Re-ask one item, telling the model what was wrong last time."""
    if not repair:
        return prompt
    return f"{prompt}\n\nYour previous answer was rejected: {repair}\nAnswer again."


def parse_answers(raw: str, keys: Sequence[str]) -> dict[str, str]:
    """Split a raw completion into per-key answers.

    A single expected key takes the whole body: cheap models frequently ignore
    the keying instruction when there is only one thing to answer.
    """
    if len(keys) == 1:
        return {keys[0]: _strip_echoed_key(raw.strip(), keys[0])}

    from_json = _parse_json_object(raw, keys)
    if from_json:
        return from_json
    return _parse_keyed_lines(raw, keys)


def _strip_echoed_key(answer: str, key: str) -> str:
    """Drop a `<key>:` prefix the model echoed back from an earlier packed ask.

    Only this key's own id is stripped, so an answer that merely contains a
    colon ("Summary: ...") is left exactly as written.
    """
    prefix = key.strip().casefold()
    head, separator, tail = answer.partition("\n")
    candidate, mark, remainder = head.partition(":")
    if not mark:
        candidate, mark, remainder = head.partition("=")
    if mark and candidate.strip().strip("`\"'*_ ").casefold() == prefix:
        return (remainder.strip() + separator + tail).strip()
    return answer


def _parse_json_object(raw: str, keys: Sequence[str]) -> dict[str, str]:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    wanted = {str(key) for key in keys}
    return {
        str(key): _as_text(value)
        for key, value in payload.items()
        if str(key) in wanted
    }


def _parse_keyed_lines(raw: str, keys: Sequence[str]) -> dict[str, str]:
    wanted = {key.strip().casefold(): key for key in keys}
    answers: dict[str, str] = {}
    # Framed on the protocol's real terminator (development/python.md).
    for line in raw.split("\n"):
        if line.strip().startswith(_ITEM_HEADER.strip()):
            continue
        match = _KEYED_LINE.match(line)
        if match is None:
            continue
        candidate = match.group("key").strip().strip("`\"'*_ ").casefold()
        key = wanted.get(candidate)
        if key is not None and key not in answers:
            answers[key] = match.group("answer").strip()
    return answers


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def chunk_keys(keys: Sequence[str], records_per_call: int) -> list[tuple[str, ...]]:
    """Split keys into packing groups, preserving input order."""
    size = max(1, records_per_call)
    return [tuple(keys[start : start + size]) for start in range(0, len(keys), size)]
