"""The domain of a text label: its values, its validation and the labeller protocol.

Nothing here knows a model service; ``bedrock.py`` implements the protocol.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

# The values of renovation_state, in the order in which they are decided.
RENOVATION_STATES = ("new_build", "renovated", "original", "needs_renovation", "unknown")


@dataclass(frozen=True)
class TextLabel:
    """What one description says: the state of the home and whether it is sold at auction."""

    renovation_state: str
    is_auction: bool


@dataclass(frozen=True)
class LabelResult:
    """One model call: its label, None for an answer outside the schema, and its tokens."""

    label: TextLabel | None
    input_tokens: int
    output_tokens: int


class TextLabeler(Protocol):
    """Labels one redacted description of a home for sale."""

    def label(self, description: str) -> LabelResult: ...


def label_from_answer(answer: object) -> TextLabel | None:
    """The label of a model's answer, or None unless it matches the answer schema exactly."""
    if not isinstance(answer, Mapping) or set(answer) != {"renovation_state", "is_auction"}:
        return None
    state, auction = answer["renovation_state"], answer["is_auction"]
    if state not in RENOVATION_STATES or not isinstance(auction, bool):
        return None
    return TextLabel(state, auction)
