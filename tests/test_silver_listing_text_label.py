"""listing_text_label: Silver's text rows joined to the newest stored answer of the label version."""

import hashlib
import unittest
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

from src.enrichment.prompt import LABEL_VERSION
from src.enrichment.text_labels import ANSWER_SCHEMA_ARROW
from src.silver.rebuild import (
    TEXT_LABEL_LISTING_COLUMNS,
    TEXT_LABEL_TABLE,
    TEXT_LABEL_TEXT_COLUMNS,
    TRANSFORMATION_VERSION,
    listing_text_labels,
)

EARLY = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
LATE = datetime(2026, 11, 21, 1, 0, tzinfo=UTC)
NULLS = {
    "renovation_state": None,
    "is_auction": None,
    "label_version": None,
    "model_id": None,
    "labeled_at": None,
}


def sha(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def listing(*rows: tuple[str, str | None]) -> pa.Table:
    """(source_listing_id, operation) per listing."""
    return pa.table(
        {
            "source": pa.array(["s"] * len(rows), pa.string()),
            "source_listing_id": pa.array([key for key, _ in rows], pa.string()),
            "operation": pa.array([operation for _, operation in rows], pa.string()),
        }
    )


def listing_text(*rows: tuple[str, str | None]) -> pa.Table:
    """(source_listing_id, description) per text row."""
    return pa.table(
        {
            "source": pa.array(["s"] * len(rows), pa.string()),
            "source_listing_id": pa.array([key for key, _ in rows], pa.string()),
            "description": pa.array([description for _, description in rows], pa.string()),
        }
    )


def answer(
    description: str,
    state: str = "renovated",
    auction: bool = False,
    *,
    version: str = LABEL_VERSION,
    model_id: str = "model-a",
    labeled_at: datetime = EARLY,
) -> dict:
    return {
        "text_sha256": sha(description),
        "label_version": version,
        "model_id": model_id,
        "renovation_state": state,
        "is_auction": auction,
        "labeled_at": labeled_at,
        "input_tokens": 100,
        "output_tokens": 10,
    }


def answers(*rows: dict) -> pa.Table:
    return pa.Table.from_pylist(list(rows), schema=ANSWER_SCHEMA_ARROW)


class ListingTextLabelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect()

    def tearDown(self) -> None:
        self.connection.close()

    def rows(self, listings: pa.Table, texts: pa.Table, stored: pa.Table) -> dict[str, dict]:
        table = listing_text_labels(self.connection, listings, texts, stored)
        return {
            row.pop("source_listing_id"): {
                key: value
                for key, value in row.items()
                if key not in ("source", "transformation_version")
            }
            for row in table.to_pylist()
        }

    def test_a_sale_listing_with_an_answer_carries_it(self) -> None:
        rows = self.rows(
            listing(("1", "sale")),
            listing_text(("1", "Piso reformado.")),
            answers(answer("Piso reformado.", "renovated", True, model_id="model-x")),
        )

        self.assertEqual(
            rows["1"],
            {
                "renovation_state": "renovated",
                "is_auction": True,
                "label_version": LABEL_VERSION,
                "model_id": "model-x",
                "labeled_at": EARLY,
            },
        )

    def test_no_answer_an_empty_description_or_another_version_give_nulls(self) -> None:
        rows = self.rows(
            listing(("1", "sale"), ("2", "sale"), ("3", "sale"), ("4", "sale")),
            listing_text(("1", "Sin respuesta."), ("2", ""), ("3", None), ("4", "Otra versión.")),
            answers(answer(""), answer("Otra versión.", version="tl0")),
        )

        for key in ("1", "2", "3", "4"):
            with self.subTest(listing=key):
                self.assertEqual(rows[key], NULLS)

    def test_a_rent_listing_has_nulls_even_with_an_answer(self) -> None:
        rows = self.rows(
            listing(("1", "rent"), ("2", None)),
            listing_text(("1", "Piso."), ("2", "Piso.")),
            answers(answer("Piso.")),
        )

        self.assertEqual((rows["1"], rows["2"]), (NULLS, NULLS))

    def test_the_newer_of_two_answers_wins(self) -> None:
        rows = self.rows(
            listing(("1", "sale")),
            listing_text(("1", "Piso.")),
            answers(
                answer("Piso.", "original", labeled_at=LATE),
                answer("Piso.", "renovated", labeled_at=EARLY),
            ),
        )

        self.assertEqual(
            (rows["1"]["renovation_state"], rows["1"]["labeled_at"]), ("original", LATE)
        )

    def test_two_listings_with_one_description_carry_the_same_answer(self) -> None:
        rows = self.rows(
            listing(("1", "sale"), ("2", "sale")),
            listing_text(("1", "Piso."), ("2", "Piso.")),
            answers(answer("Piso.", "needs_renovation")),
        )

        self.assertEqual(rows["1"], rows["2"])
        self.assertEqual(rows["1"]["renovation_state"], "needs_renovation")

    def test_a_non_ascii_description_meets_the_answer_keyed_by_hashlib(self) -> None:
        description = "Ático en Russafa — ¡a estrenar tras reforma! 😀"

        rows = self.rows(
            listing(("1", "sale")), listing_text(("1", description)), answers(answer(description))
        )

        self.assertEqual(rows["1"]["renovation_state"], "renovated")

    def test_the_table_has_the_columns_of_the_conventions_one_row_per_text_row(self) -> None:
        texts = listing_text(("3", "C."), ("1", "A."), ("2", None))
        stored = answers(answer("A."))

        table = listing_text_labels(
            self.connection, listing(("1", "sale"), ("2", "sale"), ("3", "sale")), texts, stored
        )

        self.assertEqual(
            table.schema,
            pa.schema(
                [
                    ("source", pa.string()),
                    ("source_listing_id", pa.string()),
                    ("renovation_state", pa.string()),
                    ("is_auction", pa.bool_()),
                    ("label_version", pa.string()),
                    ("model_id", pa.string()),
                    ("labeled_at", pa.timestamp("us", tz="UTC")),
                    ("transformation_version", pa.string()),
                ]
            ),
        )
        self.assertEqual(table.column("source_listing_id").to_pylist(), ["1", "2", "3"])
        self.assertEqual(
            set(table.column("transformation_version").to_pylist()), {TRANSFORMATION_VERSION}
        )
        relations = self.connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
        self.assertEqual(
            [name for (name,) in relations if name.startswith(("text_label_", "silver_"))], []
        )

    def test_the_constants(self) -> None:
        self.assertEqual(TEXT_LABEL_TABLE, "listing_text_label")
        self.assertEqual(TEXT_LABEL_LISTING_COLUMNS, ("source", "source_listing_id", "operation"))
        self.assertEqual(TEXT_LABEL_TEXT_COLUMNS, ("source", "source_listing_id", "description"))


if __name__ == "__main__":
    unittest.main()
