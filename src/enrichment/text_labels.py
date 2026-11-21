"""The stored answers of the labeller, the records of its batches, and the texts still to send."""

from pathlib import Path

import duckdb
import pyarrow as pa

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
