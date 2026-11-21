"""The stored answers of the labeller, the records of its batches, and the texts still to send.

A run first collects the batches that have ended into parts, then submits the pending texts as
one batch with its record. Nothing here waits: a batch that has not ended is left for a later run.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from src.enrichment.batches import BatchAnswer, TextLabelBatches
from src.enrichment.prompt import LABEL_VERSION

SQL_DIR = Path(__file__).resolve().parent / "sql"

# One stored answer, written in one part per collected batch.
ANSWER_SCHEMA_ARROW = pa.schema(
    [
        ("text_sha256", pa.string()),
        ("label_version", pa.string()),
        ("model_id", pa.string()),
        ("renovation_state", pa.string()),
        ("is_auction", pa.bool_()),
        ("labeled_at", pa.timestamp("us", tz="UTC")),
        ("input_tokens", pa.int32()),
        ("output_tokens", pa.int32()),
    ]
)

# One text of a submitted batch; the batch is open while its record exists and its part does not.
BATCH_RECORD_SCHEMA_ARROW = pa.schema(
    [
        ("text_sha256", pa.string()),
        ("label_version", pa.string()),
        ("model_id", pa.string()),
        ("batch_id", pa.string()),
        ("submitted_at", pa.timestamp("us", tz="UTC")),
    ]
)

_INPUTS = ("pending_listing", "pending_listing_text", "pending_stored", "pending_open_texts")


def pending_texts(
    connection: duckdb.DuckDBPyConnection,
    listing: pa.Table,
    listing_text: pa.Table,
    stored: pa.Table,
    open_texts: pa.Table,
) -> pa.Table:
    """The texts to send under ``LABEL_VERSION``: text_sha256 and description, by text_sha256.

    ``listing`` needs source, source_listing_id and operation; ``listing_text`` source,
    source_listing_id and description; ``stored`` and ``open_texts`` (the rows of the records of
    open batches) text_sha256 and label_version.
    """
    tables = (
        listing.select(["source", "source_listing_id", "operation"]),
        listing_text.select(["source", "source_listing_id", "description"]),
        stored.select(["text_sha256", "label_version"]),
        open_texts.select(["text_sha256", "label_version"]),
    )
    connection.execute("SET VARIABLE label_version = ?", [LABEL_VERSION])
    for name, table in zip(_INPUTS, tables, strict=True):
        connection.register(name, table)
    try:
        return connection.execute(
            (SQL_DIR / "pending_text.sql").read_text(encoding="utf-8")
        ).to_arrow_table()
    finally:
        for name in _INPUTS:
            connection.unregister(name)


class BatchResultError(ValueError):
    """A batch answered for a text that its record does not hold."""


class LabelVersionError(ValueError):
    """Stored answers of the label version came from another model."""


@dataclass(frozen=True)
class BatchRecord:
    """An open batch as its record holds it."""

    batch_id: str
    model_id: str
    text_sha256s: tuple[str, ...]


@dataclass(frozen=True)
class CollectReport:
    """Counts of one collection; failed_batches held requests and not one that succeeded."""

    collected: int = 0
    still_open: int = 0
    labelled: int = 0
    invalid: int = 0
    errored: int = 0
    expired: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failed_batches: int = 0


@dataclass(frozen=True)
class SubmitReport:
    """Counts of one submission."""

    pending: int
    submitted: int
    remaining: int


def _answer_part(record: BatchRecord, answers: list[BatchAnswer], labeled_at: datetime) -> pa.Table:
    rows = sorted(
        (
            {
                "text_sha256": answer.text_sha256,
                "label_version": LABEL_VERSION,
                "model_id": record.model_id,
                "renovation_state": answer.result.label.renovation_state,
                "is_auction": answer.result.label.is_auction,
                "labeled_at": labeled_at,
                "input_tokens": answer.result.input_tokens,
                "output_tokens": answer.result.output_tokens,
            }
            for answer in answers
            if answer.outcome == "succeeded" and answer.result.label is not None
        ),
        key=lambda row: row["text_sha256"],
    )
    return pa.Table.from_pylist(rows, schema=ANSWER_SCHEMA_ARROW)


def _counts(answers: list[BatchAnswer]) -> CollectReport:
    results = [answer.result for answer in answers if answer.outcome == "succeeded"]
    return CollectReport(
        collected=1,
        labelled=sum(result.label is not None for result in results),
        invalid=sum(result.label is None for result in results),
        errored=sum(answer.outcome == "errored" for answer in answers),
        expired=sum(answer.outcome in ("expired", "canceled") for answer in answers),
        input_tokens=sum(result.input_tokens for result in results),
        output_tokens=sum(result.output_tokens for result in results),
        failed_batches=int(bool(answers) and not results),
    )


def _add(left: CollectReport, right: CollectReport) -> CollectReport:
    return CollectReport(
        **{name: getattr(left, name) + getattr(right, name) for name in left.__dataclass_fields__}
    )


def collect_batches(
    open_batches: Sequence[BatchRecord],
    batches: TextLabelBatches,
    *,
    write_part: Callable[[str, pa.Table], None],
) -> CollectReport:
    """Write one part of ``ANSWER_SCHEMA_ARROW`` per open batch that has ended, in batch id order.

    A batch whose results can no longer be read is closed with a part without a row. Every row
    carries the record's model id and the batch's end as labeled_at.
    """
    report = CollectReport()
    for record in sorted(open_batches, key=lambda record: record.batch_id):
        state = batches.state(record.batch_id)
        if not state.ended:
            report = _add(report, CollectReport(still_open=1))
            continue
        answers = list(batches.answers(record.batch_id)) if state.readable else []
        known = set(record.text_sha256s)
        if any(answer.text_sha256 not in known for answer in answers):
            raise BatchResultError(
                f"batch {record.batch_id} answered for a text that its record does not hold"
            )
        write_part(record.batch_id, _answer_part(record, answers, state.ended_at))
        report = _add(report, _counts(answers))
    return report


def submit_pending(
    pending: pa.Table,
    batches: TextLabelBatches,
    *,
    model_id: str,
    max_texts: int,
    write_record: Callable[[str, pa.Table], None],
    now: Callable[[], datetime],
) -> SubmitReport:
    """Submit the first ``max_texts`` pending texts as one batch, then write its record."""
    chosen = pending.slice(0, max_texts)
    if chosen.num_rows:
        texts = list(
            zip(
                chosen.column("text_sha256").to_pylist(),
                chosen.column("description").to_pylist(),
                strict=True,
            )
        )
        batch_id = batches.submit(texts)
        submitted_at = now()
        rows = [
            {
                "text_sha256": text_sha256,
                "label_version": LABEL_VERSION,
                "model_id": model_id,
                "batch_id": batch_id,
                "submitted_at": submitted_at,
            }
            for text_sha256 in sorted(text_sha256 for text_sha256, _ in texts)
        ]
        write_record(batch_id, pa.Table.from_pylist(rows, schema=BATCH_RECORD_SCHEMA_ARROW))
    return SubmitReport(
        pending=pending.num_rows,
        submitted=chosen.num_rows,
        remaining=pending.num_rows - chosen.num_rows,
    )


def check_model(stored: pa.Table, model_id: str) -> None:
    """Refuse a run whose model differs from the one of the stored answers of ``LABEL_VERSION``."""
    others = sorted(
        {
            row["model_id"]
            for row in stored.select(["label_version", "model_id"]).to_pylist()
            if row["label_version"] == LABEL_VERSION and row["model_id"] != model_id
        }
    )
    if others:
        raise LabelVersionError(
            f"stored answers of {LABEL_VERSION} come from {', '.join(others)}, "
            f"not from {model_id}; another model needs a new label version"
        )
