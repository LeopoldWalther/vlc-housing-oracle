"""Silver observation keeps one copy per listing and day, chosen the same way on every rebuild."""

import tempfile
import unittest
from collections.abc import Sequence
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


DAY = date(2026, 10, 1)


def card(
    listing_id: str,
    municipality: str,
    *,
    price_eur: int | None = None,
    is_empty_card: bool = False,
    day: date = DAY,
) -> dict[str, object]:
    return {
        "source": "syn",
        "area": f"{municipality}-rent",
        "operation": "rent",
        "municipality": municipality,
        "source_listing_id": listing_id,
        "observed_at": datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC),
        "observed_date": day,
        "price_eur": price_eur,
        "sqm_built": None if is_empty_card else 70,
        "is_empty_card": is_empty_card,
    }


def silver_observation(cards: Sequence[dict[str, object]]) -> pa.Table:
    with tempfile.TemporaryDirectory() as directory:
        write_one_run(Path(directory), {"observation": cards})
        connection = duckdb.connect()
        register_one_run(connection, Path(directory))
        observation = build_tables(connection)["observation"]
        connection.close()
    return observation


class TestSilverObservation(unittest.TestCase):
    def test_a_priced_copy_beats_padding_in_a_neighbouring_area(self) -> None:
        rows = silver_observation(
            [
                card("syn-1", "alba", is_empty_card=True),
                card("syn-1", "zarza", price_eur=950),
            ]
        ).to_pylist()

        self.assertEqual(1, len(rows))
        self.assertEqual(
            ("zarza-rent", 950, False),
            (rows[0]["area"], rows[0]["price_eur"], rows[0]["is_empty_card"]),
        )

    def test_two_padding_copies_resolve_to_the_first_municipality(self) -> None:
        cards = [
            card("syn-2", "zarza", is_empty_card=True),
            card("syn-2", "alba", is_empty_card=True),
        ]

        for ordering in (cards, list(reversed(cards))):
            with self.subTest(first_in_landing=ordering[0]["municipality"]):
                rows = silver_observation(ordering).to_pylist()
                self.assertEqual(["alba-rent"], [row["area"] for row in rows])

    def test_a_lone_empty_card_is_kept_and_flagged(self) -> None:
        rows = silver_observation([card("syn-3", "alba", is_empty_card=True)]).to_pylist()

        self.assertEqual([True], [row["is_empty_card"] for row in rows])

    def test_an_all_null_price_column_stays_an_integer(self) -> None:
        observation = silver_observation([card("syn-4", "alba"), card("syn-5", "alba")])

        self.assertEqual(pa.int32(), observation.schema.field("price_eur").type)

    def test_one_row_per_listing_and_day(self) -> None:
        later = date(2026, 10, 2)
        observation = silver_observation(
            [
                card("syn-6", "alba", price_eur=1000),
                card("syn-6", "zarza", price_eur=1000),
                card("syn-6", "alba", price_eur=990, day=later),
                card("syn-7", "alba", price_eur=500),
            ]
        )

        keys = [
            (row["source"], row["source_listing_id"], row["observed_date"])
            for row in observation.to_pylist()
        ]
        self.assertEqual(3, len(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(
            {"silver-v1"}, set(observation.column("transformation_version").to_pylist())
        )


if __name__ == "__main__":
    unittest.main()
