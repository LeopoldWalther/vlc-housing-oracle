"""The labeller's state: the texts still to send, collecting ended batches, submitting pending ones."""

import hashlib
import unittest
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

from src.enrichment.batches import BatchAnswer, BatchState
from src.enrichment.labeler import LabelResult, TextLabel
from src.enrichment.prompt import LABEL_VERSION
from src.enrichment.text_labels import (
    ANSWER_SCHEMA_ARROW,
    BATCH_RECORD_SCHEMA_ARROW,
    BatchRecord,
    BatchResultError,
    CollectReport,
    LabelVersionError,
    SubmitReport,
    check_model,
    collect_batches,
    pending_texts,
    submit_pending,
)


def sha(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def listing(*rows: tuple[str, str]) -> pa.Table:
    """(source_listing_id, operation) per listing, all of one source."""
    return pa.table(
        {
            "source": ["s"] * len(rows),
            "source_listing_id": [listing_id for listing_id, _ in rows],
            "operation": [operation for _, operation in rows],
        }
    )


def listing_text(*rows: tuple[str, str | None]) -> pa.Table:
    """(source_listing_id, description) per text row, all of one source."""
    return pa.table(
        {
            "source": pa.array(["s"] * len(rows), pa.string()),
            "source_listing_id": pa.array([listing_id for listing_id, _ in rows], pa.string()),
            "description": pa.array([description for _, description in rows], pa.string()),
        }
    )


def stored(*rows: tuple[str, str]) -> pa.Table:
    """(description, label_version) per stored answer."""
    return pa.table(
        {
            "text_sha256": pa.array([sha(description) for description, _ in rows], pa.string()),
            "label_version": pa.array([version for _, version in rows], pa.string()),
        }
    )


def open_texts(*rows: tuple[str, str]) -> pa.Table:
    """(description, label_version) per row of an open batch's record."""
    return stored(*rows)


class PendingTextsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect()

    def tearDown(self) -> None:
        self.connection.close()

    def pending(self, listings, texts, answers=None, batches=None) -> list[dict]:
        return pending_texts(
            self.connection,
            listings,
            texts,
            answers if answers is not None else stored(),
            batches if batches is not None else open_texts(),
        ).to_pylist()

    def test_a_sale_description_without_an_answer_is_pending_and_a_rent_one_never(self) -> None:
        rows = self.pending(
            listing(("1", "sale"), ("2", "rent")),
            listing_text(("1", "Piso reformado."), ("2", "Piso en alquiler.")),
        )

        self.assertEqual(
            rows, [{"text_sha256": sha("Piso reformado."), "description": "Piso reformado."}]
        )

    def test_two_sale_listings_with_one_description_are_one_row(self) -> None:
        rows = self.pending(
            listing(("1", "sale"), ("2", "sale")),
            listing_text(("1", "Piso."), ("2", "Piso.")),
        )

        self.assertEqual(rows, [{"text_sha256": sha("Piso."), "description": "Piso."}])

    def test_a_null_or_blank_description_is_never_pending(self) -> None:
        rows = self.pending(
            listing(("1", "sale"), ("2", "sale"), ("3", "sale")),
            listing_text(("1", None), ("2", "   \n\t "), ("3", "")),
        )

        self.assertEqual(rows, [])

    def test_an_answer_under_the_label_version_removes_the_text_another_version_does_not(
        self,
    ) -> None:
        rows = self.pending(
            listing(("1", "sale"), ("2", "sale")),
            listing_text(("1", "Uno."), ("2", "Dos.")),
            answers=stored(("Uno.", LABEL_VERSION), ("Dos.", "tl0")),
        )

        self.assertEqual([row["description"] for row in rows], ["Dos."])

    def test_a_text_in_an_open_batch_of_the_label_version_is_not_pending(self) -> None:
        rows = self.pending(
            listing(("1", "sale"), ("2", "sale")),
            listing_text(("1", "Uno."), ("2", "Dos.")),
            batches=open_texts(("Uno.", LABEL_VERSION), ("Dos.", "tl0")),
        )

        self.assertEqual([row["description"] for row in rows], ["Dos."])

    def test_the_hash_is_sha256_of_the_utf8_bytes(self) -> None:
        description = "Ático reformado en Russafa, cocina con isla — ¡a estrenar! 😀"

        rows = self.pending(listing(("1", "sale")), listing_text(("1", description)))

        self.assertEqual(rows[0]["text_sha256"], sha(description))

    def test_rows_are_ordered_by_hash_and_the_session_keeps_no_relation(self) -> None:
        descriptions = [f"Piso número {number}." for number in range(20)]
        listings = listing(*[(str(number), "sale") for number in range(20)])
        texts = listing_text(*[(str(number), text) for number, text in enumerate(descriptions)])

        result = pending_texts(self.connection, listings, texts, stored(), open_texts())

        self.assertEqual(result.column_names, ["text_sha256", "description"])
        self.assertEqual(result.column("text_sha256").to_pylist(), sorted(map(sha, descriptions)))
        relations = self.connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
        self.assertEqual(relations, [])


class SchemaTest(unittest.TestCase):
    def test_the_stored_answer_schema(self) -> None:
        self.assertEqual(
            ANSWER_SCHEMA_ARROW,
            pa.schema(
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
            ),
        )

    def test_the_batch_record_schema(self) -> None:
        self.assertEqual(
            BATCH_RECORD_SCHEMA_ARROW,
            pa.schema(
                [
                    ("text_sha256", pa.string()),
                    ("label_version", pa.string()),
                    ("model_id", pa.string()),
                    ("batch_id", pa.string()),
                    ("submitted_at", pa.timestamp("us", tz="UTC")),
                ]
            ),
        )


ENDED_AT = datetime(2026, 11, 22, 4, 5, 6, tzinfo=UTC)
NOW = datetime(2026, 11, 22, 9, 0, 0, tzinfo=UTC)


def h(name: str) -> str:
    return sha(name)


def succeeded(
    name: str, label: TextLabel | None, tokens: tuple[int, int] = (100, 10)
) -> BatchAnswer:
    return BatchAnswer(h(name), "succeeded", LabelResult(label, *tokens))


def failed(name: str, outcome: str) -> BatchAnswer:
    return BatchAnswer(h(name), outcome, None)


class FakeBatches:
    """Prepared states and answers per batch id; records every call."""

    def __init__(self, states=None, answers=None, submit_error: Exception | None = None) -> None:
        self.states: dict[str, BatchState] = states or {}
        self.prepared: dict[str, list[BatchAnswer]] = answers or {}
        self.submit_error = submit_error
        self.submitted: list[list[tuple[str, str]]] = []
        self.answer_calls: list[str] = []

    def submit(self, texts: Sequence[tuple[str, str]]) -> str:
        if self.submit_error is not None:
            raise self.submit_error
        self.submitted.append(list(texts))
        return "batch-new"

    def state(self, batch_id: str) -> BatchState:
        return self.states[batch_id]

    def answers(self, batch_id: str) -> Iterator[BatchAnswer]:
        self.answer_calls.append(batch_id)
        return iter(self.prepared[batch_id])


def ended(readable: bool = True) -> BatchState:
    return BatchState(ended=True, ended_at=ENDED_AT, readable=readable)


RUNNING = BatchState(ended=False, ended_at=None, readable=False)


def record(batch_id: str, *names: str, model_id: str = "model-a") -> BatchRecord:
    return BatchRecord(batch_id, model_id, tuple(h(name) for name in names))


class CollectBatchesTest(unittest.TestCase):
    def collect(self, open_batches, fake: FakeBatches) -> tuple[CollectReport, dict[str, pa.Table]]:
        parts: dict[str, pa.Table] = {}

        def write_part(batch_id: str, table: pa.Table) -> None:
            parts[batch_id] = table

        return collect_batches(open_batches, fake, write_part=write_part), parts

    def test_two_valid_answers_and_one_invalid_give_one_part_of_two_rows(self) -> None:
        fake = FakeBatches(
            {"b1": ended()},
            {
                "b1": [
                    succeeded("c", TextLabel("renovated", False), (300, 30)),
                    succeeded("a", None, (100, 10)),
                    succeeded("b", TextLabel("original", True), (200, 20)),
                ]
            },
        )

        report, parts = self.collect([record("b1", "a", "b", "c")], fake)

        self.assertEqual(
            report,
            CollectReport(
                collected=1,
                still_open=0,
                labelled=2,
                invalid=1,
                errored=0,
                expired=0,
                input_tokens=600,
                output_tokens=60,
                failed_batches=0,
            ),
        )
        part = parts["b1"]
        self.assertEqual(part.schema, ANSWER_SCHEMA_ARROW)
        self.assertEqual(part.column("text_sha256").to_pylist(), sorted([h("b"), h("c")]))
        by_hash = {row["text_sha256"]: row for row in part.to_pylist()}
        self.assertEqual(
            by_hash[h("c")],
            {
                "text_sha256": h("c"),
                "label_version": LABEL_VERSION,
                "model_id": "model-a",
                "renovation_state": "renovated",
                "is_auction": False,
                "labeled_at": ENDED_AT,
                "input_tokens": 300,
                "output_tokens": 30,
            },
        )
        self.assertEqual(by_hash[h("b")]["renovation_state"], "original")
        self.assertIs(by_hash[h("b")]["is_auction"], True)

    def test_a_batch_that_has_not_ended_is_left_alone(self) -> None:
        fake = FakeBatches({"b1": RUNNING}, {})

        report, parts = self.collect([record("b1", "a")], fake)

        self.assertEqual(parts, {})
        self.assertEqual(fake.answer_calls, [])
        self.assertEqual((report.collected, report.still_open), (0, 1))

    def test_failed_requests_are_counted_and_a_batch_of_only_them_is_a_failed_batch(self) -> None:
        fake = FakeBatches(
            {"b1": ended(), "b2": ended()},
            {
                "b1": [failed("a", "errored"), failed("b", "expired"), failed("c", "canceled")],
                "b2": [failed("d", "errored"), succeeded("e", TextLabel("unknown", False))],
            },
        )

        report, parts = self.collect([record("b2", "d", "e"), record("b1", "a", "b", "c")], fake)

        self.assertEqual(parts["b1"].num_rows, 0)
        self.assertEqual(parts["b1"].schema, ANSWER_SCHEMA_ARROW)
        self.assertEqual(parts["b2"].column("text_sha256").to_pylist(), [h("e")])
        self.assertEqual(
            (report.collected, report.errored, report.expired, report.labelled),
            (2, 2, 2, 1),
        )
        self.assertEqual(report.failed_batches, 1)

    def test_an_ended_batch_that_is_not_readable_gets_an_empty_part(self) -> None:
        fake = FakeBatches({"b1": ended(readable=False)}, {})

        report, parts = self.collect([record("b1", "a")], fake)

        self.assertEqual(parts["b1"].num_rows, 0)
        self.assertEqual(parts["b1"].schema, ANSWER_SCHEMA_ARROW)
        self.assertEqual(fake.answer_calls, [])
        self.assertEqual((report.collected, report.failed_batches), (1, 0))

    def test_rows_carry_the_records_model_not_another(self) -> None:
        fake = FakeBatches({"b1": ended()}, {"b1": [succeeded("a", TextLabel("original", False))]})

        _, parts = self.collect([record("b1", "a", model_id="model-old")], fake)

        row = parts["b1"].to_pylist()[0]
        self.assertEqual(
            (row["label_version"], row["model_id"], row["labeled_at"]),
            (LABEL_VERSION, "model-old", ENDED_AT),
        )

    def test_an_answer_outside_the_record_raises_and_writes_nothing_of_that_batch(self) -> None:
        fake = FakeBatches(
            {"b1": ended()},
            {"b1": [succeeded("a", TextLabel("original", False)), succeeded("x", None)]},
        )

        with self.assertRaises(BatchResultError) as caught:
            self.collect([record("b1", "a")], fake)
        self.assertIn("b1", str(caught.exception))
        self.assertNotIn(h("x"), str(caught.exception))

    def test_batches_are_handled_in_the_order_of_their_id(self) -> None:
        order: list[str] = []
        fake = FakeBatches(
            {"b2": ended(), "b1": ended()},
            {"b1": [], "b2": []},
        )
        collect_batches(
            [record("b2", "a"), record("b1", "b")],
            fake,
            write_part=lambda batch_id, table: order.append(batch_id),
        )

        self.assertEqual(order, ["b1", "b2"])


def pending_table(count: int) -> pa.Table:
    names = [f"text {number:04d}" for number in range(count)]
    return pa.table({"text_sha256": [h(name) for name in names], "description": names})


class SubmitPendingTest(unittest.TestCase):
    def submit(self, pending: pa.Table, fake: FakeBatches, max_texts: int = 300):
        records: list[tuple[str, pa.Table]] = []
        report = submit_pending(
            pending,
            fake,
            model_id="model-a",
            max_texts=max_texts,
            write_record=lambda batch_id, table: records.append((batch_id, table)),
            now=lambda: NOW,
        )
        return report, records

    def test_the_first_texts_up_to_the_cap_are_one_batch_with_one_record(self) -> None:
        pending = pending_table(450)
        fake = FakeBatches()

        report, records = self.submit(pending, fake)

        self.assertEqual(report, SubmitReport(pending=450, submitted=300, remaining=150))
        expected = list(
            zip(
                pending.column("text_sha256").to_pylist()[:300],
                pending.column("description").to_pylist()[:300],
                strict=True,
            )
        )
        self.assertEqual(fake.submitted, [expected])
        self.assertEqual(len(records), 1)
        batch_id, table = records[0]
        self.assertEqual(batch_id, "batch-new")
        self.assertEqual(table.schema, BATCH_RECORD_SCHEMA_ARROW)
        self.assertEqual(table.num_rows, 300)
        self.assertEqual(
            table.column("text_sha256").to_pylist(), sorted(hash_ for hash_, _ in expected)
        )
        self.assertEqual(
            table.slice(0, 1).to_pylist()[0] | {"text_sha256": None},
            {
                "text_sha256": None,
                "label_version": LABEL_VERSION,
                "model_id": "model-a",
                "batch_id": "batch-new",
                "submitted_at": NOW,
            },
        )

    def test_nothing_pending_submits_nothing(self) -> None:
        fake = FakeBatches()

        report, records = self.submit(pending_table(0), fake)

        self.assertEqual(report, SubmitReport(pending=0, submitted=0, remaining=0))
        self.assertEqual((fake.submitted, records), ([], []))

    def test_a_submit_that_raises_writes_no_record(self) -> None:
        records: list[tuple[str, pa.Table]] = []

        with self.assertRaises(RuntimeError):
            submit_pending(
                pending_table(3),
                FakeBatches(submit_error=RuntimeError("refused")),
                model_id="model-a",
                max_texts=300,
                write_record=lambda batch_id, table: records.append((batch_id, table)),
                now=lambda: NOW,
            )
        self.assertEqual(records, [])


class CheckModelTest(unittest.TestCase):
    def stored_models(self, *rows: tuple[str, str]) -> pa.Table:
        return pa.table(
            {
                "label_version": pa.array([version for version, _ in rows], pa.string()),
                "model_id": pa.array([model for _, model in rows], pa.string()),
            }
        )

    def test_another_model_under_the_same_version_raises(self) -> None:
        with self.assertRaises(LabelVersionError) as caught:
            check_model(self.stored_models((LABEL_VERSION, "model-old")), "model-new")
        for part in (LABEL_VERSION, "model-old", "model-new"):
            self.assertIn(part, str(caught.exception))

    def test_the_same_model_another_version_or_nothing_stored_passes(self) -> None:
        cases = {
            "same model": self.stored_models((LABEL_VERSION, "model-a")),
            "another version": self.stored_models(("tl0", "model-old")),
            "nothing stored": self.stored_models(),
        }
        for case, stored_models in cases.items():
            with self.subTest(case=case):
                check_model(stored_models, "model-a")


if __name__ == "__main__":
    unittest.main()
