"""The texts the labeller still has to send: sale descriptions with no answer and no open batch."""

import hashlib
import unittest

import duckdb
import pyarrow as pa

from src.enrichment.prompt import LABEL_VERSION
from src.enrichment.text_labels import (
    ANSWER_SCHEMA_ARROW,
    BATCH_RECORD_SCHEMA_ARROW,
    pending_texts,
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


if __name__ == "__main__":
    unittest.main()
