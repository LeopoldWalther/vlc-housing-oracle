"""The labeller's view of a message batch: submit texts, ask whether it ended, read its answers.

Nothing here knows a model service; ``claude_api.py`` implements the protocol.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from src.enrichment.labeler import LabelResult

Outcome = Literal["succeeded", "errored", "expired", "canceled"]


@dataclass(frozen=True)
class BatchState:
    """Whether a batch has ended, when, and whether its results can still be read."""

    ended: bool
    ended_at: datetime | None
    readable: bool


@dataclass(frozen=True)
class BatchAnswer:
    """One request of a batch: its text's hash, its outcome and, if it succeeded, its result."""

    text_sha256: str
    outcome: Outcome
    result: LabelResult | None


class TextLabelBatches(Protocol):
    """Labels redacted descriptions in batches that end some time after they are submitted."""

    def submit(self, texts: Sequence[tuple[str, str]]) -> str:
        """Submit (text_sha256, description) pairs as one batch and return its id."""
        ...

    def state(self, batch_id: str) -> BatchState: ...

    def answers(self, batch_id: str) -> Iterator[BatchAnswer]:
        """The answers of an ended batch, in any order, keyed by the text's hash."""
        ...
