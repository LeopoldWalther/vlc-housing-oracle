"""The labeller's state: the texts still to send, collecting ended batches, submitting pending ones."""

import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog

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
    main,
    pending_texts,
    stored_answers,
    submit_pending,
)
from src.silver.rebuild import main as silver_main
from src.synthetic.landing import generate_landing_v3


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


class ScriptedBatches:
    """Fake batches across runs: each submitted batch runs until ``end`` answers it."""

    def __init__(self) -> None:
        self.submitted: dict[str, list[tuple[str, str]]] = {}
        self.ended_answers: dict[str, list[BatchAnswer]] = {}

    def submit(self, texts: Sequence[tuple[str, str]]) -> str:
        batch_id = f"msgbatch_{len(self.submitted) + 1:02d}"
        self.submitted[batch_id] = list(texts)
        return batch_id

    def end(self, batch_id: str, outcome_of=lambda description: "succeeded") -> None:
        answers = []
        for text_sha256, description in self.submitted[batch_id]:
            outcome = outcome_of(description)
            result = LabelResult(TextLabel("original", False), 120, 12)
            answers.append(
                BatchAnswer(text_sha256, outcome, result if outcome == "succeeded" else None)
            )
        self.ended_answers[batch_id] = answers

    def state(self, batch_id: str) -> BatchState:
        if batch_id in self.ended_answers:
            return BatchState(ended=True, ended_at=ENDED_AT, readable=True)
        return RUNNING

    def answers(self, batch_id: str) -> Iterator[BatchAnswer]:
        return iter(self.ended_answers[batch_id])


REPORT_LINES = (
    "batches collected",
    "batches open",
    "labelled",
    "invalid answers",
    "errored requests",
    "expired requests",
    "input tokens",
    "output tokens",
    "pending",
    "submitted",
    "remaining",
)


class TextLabelCommandTest(unittest.TestCase):
    """The command against a catalog backfilled once from the synthetic landing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        landing = cls.root / "landing" / "v3"
        landing.mkdir(parents=True)
        generate_landing_v3(landing, seed=1)
        # PyIceberg reads its environment once, at import; both commands get one SQLite catalog.
        catalog = SqlCatalog(
            "test", uri=f"sqlite:///{cls.root}/catalog.db", warehouse=f"file://{cls.root}"
        )
        for target in (
            "src.silver.rebuild.load_catalog",
            "src.enrichment.text_labels.load_catalog",
        ):
            cls.enterClassContext(mock.patch(target, return_value=catalog))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = silver_main(
                ["--landing", str(landing), "--catalog", "test", "--mode", "backfill"]
            )
        if code != 0:
            raise RuntimeError(f"the Silver backfill of the fixture exited {code}")

    def setUp(self) -> None:
        run = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.labels = run / "labels"
        self.batches_dir = run / "batches"
        self.fake = ScriptedBatches()

    def run_main(self, *extra: str, max_texts: int = 300, batches=None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(
                [
                    "--catalog",
                    "test",
                    "--labels",
                    str(self.labels),
                    "--batches",
                    str(self.batches_dir),
                    "--model-id",
                    "model-a",
                    "--max-texts",
                    str(max_texts),
                    *extra,
                ],
                batches=self.fake if batches is None else batches,
                now=lambda: NOW,
            )
        return code, out.getvalue(), err.getvalue()

    def files(self) -> list[str]:
        return sorted(
            str(path.relative_to(path.parents[3]))
            for directory in (self.labels, self.batches_dir)
            if directory.exists()
            for path in directory.rglob("*.parquet")
        )

    def report(self, output: str) -> dict[str, int]:
        lines = [line.split(": ") for line in output.strip().splitlines()]
        self.assertEqual(tuple(name for name, _ in lines), REPORT_LINES)
        return {name: int(value) for name, value in lines}

    def test_a_first_run_submits_every_distinct_text_and_writes_one_record(self) -> None:
        code, output, _ = self.run_main()

        self.assertEqual(code, 0)
        ((batch_id, texts),) = self.fake.submitted.items()
        self.assertEqual(len(texts), len({text_sha256 for text_sha256, _ in texts}))
        self.assertGreaterEqual(len(texts), 2)
        for text_sha256, description in texts:
            self.assertEqual(text_sha256, sha(description))
            self.assertTrue(description.strip())
        self.assertEqual(
            self.files(),
            [f"batches/label_version={LABEL_VERSION}/batch={batch_id}/record.parquet"],
        )
        record_table = pq.read_table(
            self.batches_dir
            / f"label_version={LABEL_VERSION}"
            / f"batch={batch_id}"
            / "record.parquet"
        )
        self.assertEqual(record_table.schema, BATCH_RECORD_SCHEMA_ARROW)
        self.assertEqual(record_table.num_rows, len(texts))
        report = self.report(output)
        self.assertEqual(
            (
                report["pending"],
                report["submitted"],
                report["remaining"],
                report["batches collected"],
            ),
            (len(texts), len(texts), 0, 0),
        )
        for text_sha256, description in texts:
            self.assertNotIn(text_sha256, output)
            self.assertNotIn(description, output)

    def test_a_run_while_the_batch_runs_does_nothing(self) -> None:
        self.run_main()
        before = self.files()

        code, output, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake.submitted), 1)
        self.assertEqual(self.files(), before)
        report = self.report(output)
        self.assertEqual(
            (report["batches open"], report["submitted"], report["pending"]), (1, 0, 0)
        )

    def test_a_run_after_the_end_writes_the_part_and_the_next_writes_nothing(self) -> None:
        self.run_main()
        ((batch_id, texts),) = self.fake.submitted.items()
        self.fake.end(batch_id)

        code, output, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertIn(
            f"labels/label_version={LABEL_VERSION}/run={batch_id}/part-0.parquet", self.files()
        )
        self.assertEqual(len(self.fake.submitted), 1)
        report = self.report(output)
        self.assertEqual(
            (report["batches collected"], report["labelled"], report["submitted"]),
            (1, len(texts), 0),
        )
        stored = stored_answers(duckdb.connect(), str(self.labels))
        self.assertEqual(stored.num_rows, len(texts))
        before = self.files()

        self.run_main()

        self.assertEqual(self.files(), before)
        self.assertEqual(len(self.fake.submitted), 1)

    def test_an_errored_text_is_submitted_again_by_the_collecting_run(self) -> None:
        self.run_main()
        ((batch_id, texts),) = self.fake.submitted.items()
        errored = texts[0][1]
        self.fake.end(
            batch_id, lambda description: "errored" if description == errored else "succeeded"
        )

        code, output, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(list(self.fake.submitted.values())[1], [texts[0]])
        self.assertEqual(self.report(output)["errored requests"], 1)

    def test_a_cap_leaves_the_rest_for_the_next_run(self) -> None:
        code, output, _ = self.run_main(max_texts=1)
        first = self.report(output)

        self.run_main(max_texts=300)

        self.assertEqual(code, 0)
        self.assertEqual((first["submitted"], first["remaining"]), (1, first["pending"] - 1))
        first_batch, second_batch = self.fake.submitted.values()
        self.assertEqual(len(second_batch), first["pending"] - 1)
        self.assertFalse({h for h, _ in first_batch} & {h for h, _ in second_batch})

    def test_a_collected_batch_without_a_succeeded_request_exits_1_after_its_work(self) -> None:
        self.run_main()
        ((batch_id, texts),) = self.fake.submitted.items()
        self.fake.end(batch_id, lambda description: "errored")

        code, output, error = self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("a collected batch had no succeeded request", error)
        self.assertIn(
            f"labels/label_version={LABEL_VERSION}/run={batch_id}/part-0.parquet", self.files()
        )
        self.assertEqual(len(self.fake.submitted[list(self.fake.submitted)[1]]), len(texts))
        self.assertEqual(self.report(output)["submitted"], len(texts))

    def test_stored_answers_of_nothing_is_an_empty_table_with_the_schema(self) -> None:
        connection = duckdb.connect()
        for location in (self.labels, self.labels.parent / "empty"):
            with self.subTest(location=location.name):
                location.parent.mkdir(parents=True, exist_ok=True)
                if location.name == "empty":
                    location.mkdir()
                stored = stored_answers(connection, str(location))
                self.assertEqual((stored.num_rows, stored.schema), (0, ANSWER_SCHEMA_ARROW))

    def test_a_part_without_a_column_exits_1_naming_the_part_and_the_column(self) -> None:
        part = self.labels / f"label_version={LABEL_VERSION}" / "run=msgbatch_x" / "part-0.parquet"
        part.parent.mkdir(parents=True)
        pq.write_table(ANSWER_SCHEMA_ARROW.empty_table().drop_columns(["model_id"]), part)

        code, _, error = self.run_main()

        self.assertEqual(code, 1)
        self.assertIn(f"label_version={LABEL_VERSION}/run=msgbatch_x/part-0.parquet", error)
        self.assertIn("model_id", error)
        self.assertEqual(self.fake.submitted, {})

    def test_answers_of_another_model_under_the_version_exit_1_and_change_nothing(self) -> None:
        part = self.labels / f"label_version={LABEL_VERSION}" / "run=msgbatch_x" / "part-0.parquet"
        part.parent.mkdir(parents=True)
        row = {
            "text_sha256": sha("x"),
            "label_version": LABEL_VERSION,
            "model_id": "model-old",
            "renovation_state": "original",
            "is_auction": False,
            "labeled_at": ENDED_AT,
            "input_tokens": 1,
            "output_tokens": 1,
        }
        pq.write_table(pa.Table.from_pylist([row], schema=ANSWER_SCHEMA_ARROW), part)
        before = self.files()

        code, _, error = self.run_main()

        self.assertEqual(code, 1)
        self.assertIn("model-old", error)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.fake.submitted, {})

    def test_without_injected_batches_and_without_the_key_the_command_exits_1(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            del os.environ["ANTHROPIC_API_KEY"]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(
                    [
                        "--catalog",
                        "test",
                        "--labels",
                        str(self.labels),
                        "--batches",
                        str(self.batches_dir),
                        "--model-id",
                        "model-a",
                        "--max-texts",
                        "5",
                    ]
                )

        self.assertEqual(code, 1)
        self.assertIn("ANTHROPIC_API_KEY", err.getvalue())
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main()
