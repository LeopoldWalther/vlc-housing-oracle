"""Silver listing_text holds the free text of the same record listing chose."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from src.silver.landing import register_landing_v3
from src.silver.rebuild import build_tables
from src.synthetic.landing import write_landing_v3

# One synthetic collection run: the FEATURE-001 rules are checked on landing v3, as Silver reads it.
RUN_ID = "20261001T060000Z"
RUN_STARTED = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
LANDING_TABLES = ("walk", "observation", "listing_detail", "listing_text")


def write_one_run(directory: Path, rows_by_table: dict) -> None:
    rows = {
        table: [{"run_id": RUN_ID} | dict(row) for row in table_rows]
        for table, table_rows in rows_by_table.items()
    }
    write_landing_v3(directory, rows, runs={RUN_ID: {"run_started_at": RUN_STARTED}})


def register_one_run(connection: duckdb.DuckDBPyConnection, directory: Path) -> None:
    register_landing_v3(
        connection,
        str(directory),
        dict.fromkeys(LANDING_TABLES, {RUN_ID}),
        {RUN_ID: RUN_STARTED},
        {},
    )


def run_file(directory: Path, table: str) -> Path:
    return directory / table / f"run_date={RUN_STARTED.date()}" / f"run={RUN_ID}" / "part-0.parquet"


def key(listing_id: str, day: int) -> dict[str, object]:
    observed_at = datetime(2026, 10, day, 9, 0, tzinfo=UTC)
    return {
        "source": "syn",
        "area": "alba-sale",
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
    }


def text(listing_id: str, day: int, title: str) -> dict[str, object]:
    return key(listing_id, day) | {
        "title": title,
        "location": "Calle Inventada, Alba",
        "description": f"Piso luminoso, version {day}.",
        "features": ["Ascensor", "Terraza"],
    }


class TestSilverListingText(unittest.TestCase):
    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write_one_run(
            directory,
            {
                "listing_detail": [key("syn-1", 1), key("syn-1", 5), key("syn-2", 3)],
                "listing_text": [
                    text("syn-1", 1, "Piso antiguo"),
                    text("syn-1", 5, "Piso reformado"),
                    text("syn-2", 3, "Ático"),
                ],
                "observation": [key("syn-3", 2) | {"is_empty_card": False}],
            },
        )
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        register_one_run(connection, directory)
        tables = build_tables(connection)
        self.listing: pa.Table = tables["listing"]
        self.rows = {row["source_listing_id"]: row for row in tables["listing_text"].to_pylist()}

    def test_text_comes_from_the_record_listing_chose(self) -> None:
        self.assertEqual("Piso reformado", self.rows["syn-1"]["title"])
        self.assertEqual(datetime(2026, 10, 5, 9, 0, tzinfo=UTC), self.rows["syn-1"]["observed_at"])

    def test_title_location_and_features_are_verbatim(self) -> None:
        row = self.rows["syn-2"]

        self.assertEqual(
            ("Ático", "Calle Inventada, Alba", ["Ascensor", "Terraza"]),
            (row["title"], row["location"], row["features"]),
        )

    def test_one_row_per_listing_with_a_detail_record(self) -> None:
        with_detail = {
            row["source_listing_id"]
            for row in self.listing.to_pylist()
            if row["detail_observed_at"] is not None
        }

        self.assertEqual({"syn-1", "syn-2"}, with_detail)
        self.assertEqual(with_detail, set(self.rows))
        self.assertEqual(date(2026, 10, 5), self.rows["syn-1"]["observed_at"].date())
        self.assertEqual(
            {"silver-v1"}, {row["transformation_version"] for row in self.rows.values()}
        )


if __name__ == "__main__":
    unittest.main()
