"""The stored answers of the labeller, the records of its batches, and the texts still to send.

    python -m src.enrichment.text_labels --catalog <name> --labels <dir-or-s3>
        --batches <dir-or-s3> --model-id <id> --max-texts <n>

A run first collects the batches that have ended into parts, then submits the pending texts as
one batch with its record. Nothing here waits: a batch that has not ended is left for a later run.
Layouts: <labels>/label_version=<v>/run=<batch_id>/part-0.parquet and
<batches>/label_version=<v>/batch=<batch_id>/record.parquet. The command prints counts only.
"""

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs
from pyiceberg.catalog import load_catalog

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


class StoredAnswerError(ValueError):
    """A stored part lacks a column of the answer schema."""


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


PART_NAME = "part-0.parquet"
RECORD_NAME = "record.parquet"
SILVER_NAMESPACE = "vlc_housing_silver"


def _filesystem(location: str) -> tuple[fs.FileSystem, str]:
    filesystem, path = fs.FileSystem.from_uri(
        location if "://" in location else str(Path(location).resolve())
    )
    return filesystem, path.rstrip("/")


def _parquet_files(filesystem: fs.FileSystem, base: str) -> list[str]:
    selector = fs.FileSelector(base, recursive=True, allow_not_found=True)
    return sorted(
        info.path
        for info in filesystem.get_file_info(selector)
        if info.type == fs.FileType.File and info.path.endswith(".parquet")
    )


def stored_answers(connection: duckdb.DuckDBPyConnection, labels: str) -> pa.Table:
    """Every stored answer under ``labels``, of every label version, in ``ANSWER_SCHEMA_ARROW``."""
    filesystem, base = _filesystem(labels)
    prefix = "s3://" if labels.startswith("s3://") else ""
    tables = [ANSWER_SCHEMA_ARROW.empty_table()]
    for path in _parquet_files(filesystem, base):
        part = connection.execute("SELECT * FROM read_parquet(?)", [prefix + path]).to_arrow_table()
        for name in ANSWER_SCHEMA_ARROW.names:
            if name not in part.column_names:
                raise StoredAnswerError(f"stored part {path[len(base) + 1 :]} has no column {name}")
        tables.append(part.select(ANSWER_SCHEMA_ARROW.names).cast(ANSWER_SCHEMA_ARROW))
    return pa.concat_tables(tables)


def open_batches(labels: str, batches: str) -> list[BatchRecord]:
    """The batches of ``LABEL_VERSION`` with a record under ``batches`` and no part under ``labels``."""
    record_fs, record_base = _filesystem(batches)
    part_fs, part_base = _filesystem(labels)
    version_dir = f"{record_base}/label_version={LABEL_VERSION}"
    records = []
    for path in _parquet_files(record_fs, version_dir):
        batch_id = path[len(version_dir) + 1 :].split("/")[0].removeprefix("batch=")
        part = f"{part_base}/label_version={LABEL_VERSION}/run={batch_id}/{PART_NAME}"
        if part_fs.get_file_info(part).type != fs.FileType.NotFound:
            continue
        table = pq.read_table(path, filesystem=record_fs)
        model_ids = set(table.column("model_id").to_pylist())
        records.append(
            BatchRecord(
                batch_id,
                model_ids.pop() if len(model_ids) == 1 else "",
                tuple(table.column("text_sha256").to_pylist()),
            )
        )
    return records


def _writer(location: str, directory: str, name: str) -> Callable[[str, pa.Table], None]:
    filesystem, base = _filesystem(location)

    def write(batch_id: str, table: pa.Table) -> None:
        folder = f"{base}/label_version={LABEL_VERSION}/{directory}={batch_id}"
        path = f"{folder}/{name}"
        if filesystem.get_file_info(path).type != fs.FileType.NotFound:
            raise FileExistsError(f"{directory}={batch_id}/{name} exists and is never replaced")
        filesystem.create_dir(folder, recursive=True)
        pq.write_table(table, path, filesystem=filesystem)

    return write


def part_writer(labels: str) -> Callable[[str, pa.Table], None]:
    """The ``write_part`` of the answers' layout; it refuses to replace a part."""
    return _writer(labels, "run", PART_NAME)


def record_writer(batches: str) -> Callable[[str, pa.Table], None]:
    """The ``write_record`` of the records' layout; it refuses to replace a record."""
    return _writer(batches, "batch", RECORD_NAME)


def _open_texts(records: Sequence[BatchRecord]) -> pa.Table:
    hashes = [text_sha256 for record in records for text_sha256 in record.text_sha256s]
    return pa.table(
        {
            "text_sha256": pa.array(hashes, pa.string()),
            "label_version": pa.array([LABEL_VERSION] * len(hashes), pa.string()),
        }
    )


def _silver(catalog_name: str, name: str, columns: tuple[str, ...]) -> pa.Table:
    catalog = load_catalog(catalog_name)
    table = catalog.load_table(f"{SILVER_NAMESPACE}.{name}")
    return table.scan(selected_fields=columns).to_arrow()


def _report(collected: CollectReport, submitted: SubmitReport) -> None:
    for name, count in (
        ("batches collected", collected.collected),
        ("batches open", collected.still_open),
        ("labelled", collected.labelled),
        ("invalid answers", collected.invalid),
        ("errored requests", collected.errored),
        ("expired requests", collected.expired),
        ("input tokens", collected.input_tokens),
        ("output tokens", collected.output_tokens),
        ("pending", submitted.pending),
        ("submitted", submitted.submitted),
        ("remaining", submitted.remaining),
    ):
        print(f"{name}: {count}")


def main(
    argv: Sequence[str] | None = None,
    *,
    batches: TextLabelBatches | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Collect ended batches, then submit pending texts; exit 0, 1 when refused or failed, 2 on usage."""
    parser = argparse.ArgumentParser(
        prog="python -m src.enrichment.text_labels",
        description="Label Silver's sale descriptions through message batches.",
    )
    parser.add_argument("--catalog", required=True, help="PyIceberg catalog name of Silver")
    parser.add_argument("--labels", required=True, help="stored answers: a directory or s3://")
    parser.add_argument("--batches", required=True, help="batch records: a directory or s3://")
    parser.add_argument("--model-id", required=True, help="a Claude API model id")
    parser.add_argument("--max-texts", required=True, type=int, help="texts per submitted batch")
    arguments = parser.parse_args(argv)
    if arguments.max_texts < 1:
        parser.error("--max-texts must be at least 1")

    if batches is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("text labels refused: ANTHROPIC_API_KEY is not set", file=sys.stderr)
            return 1
        import anthropic

        from src.enrichment.claude_api import ClaudeApiBatches

        batches = ClaudeApiBatches(anthropic.Anthropic(max_retries=5), arguments.model_id)

    with duckdb.connect() as connection:
        if any(location.startswith("s3://") for location in (arguments.labels, arguments.batches)):
            _read_s3_through_duckdb(connection)
        try:
            check_model(stored_answers(connection, arguments.labels), arguments.model_id)
            collected = collect_batches(
                open_batches(arguments.labels, arguments.batches),
                batches,
                write_part=part_writer(arguments.labels),
            )
            pending = pending_texts(
                connection,
                _silver(arguments.catalog, "listing", ("source", "source_listing_id", "operation")),
                _silver(
                    arguments.catalog,
                    "listing_text",
                    ("source", "source_listing_id", "description"),
                ),
                stored_answers(connection, arguments.labels),
                _open_texts(open_batches(arguments.labels, arguments.batches)),
            )
        except (LabelVersionError, StoredAnswerError, BatchResultError) as error:
            print(f"text labels refused: {error}", file=sys.stderr)
            return 1
    submitted = submit_pending(
        pending,
        batches,
        model_id=arguments.model_id,
        max_texts=arguments.max_texts,
        write_record=record_writer(arguments.batches),
        now=now,
    )
    _report(collected, submitted)
    if collected.failed_batches:
        print("text labels failed: a collected batch had no succeeded request", file=sys.stderr)
        return 1
    return 0


def _read_s3_through_duckdb(connection: duckdb.DuckDBPyConnection) -> None:
    # As Silver's command: the image pre-installs both extensions, and the secret resolves the
    # task role through the AWS credential chain.
    connection.execute("LOAD httpfs")
    connection.execute("LOAD aws")
    connection.execute(
        "CREATE OR REPLACE SECRET text_labels_s3 (TYPE s3, PROVIDER credential_chain)"
    )


if __name__ == "__main__":
    sys.exit(main())
