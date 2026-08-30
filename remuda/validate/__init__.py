"""Answer validation per field kind (FR-2, FR-4)."""

from remuda.validate.answers import evaluate_map, validate_answer
from remuda.validate.preconditions import precondition_met
from remuda.validate.verdict import Outcome, Verdict

__all__ = [
    "Outcome",
    "Verdict",
    "evaluate_map",
    "precondition_met",
    "validate_answer",
]
