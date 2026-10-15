"""Synthetic landing tables are written exactly as the contract types them."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from src.contracts import landing_v1_columns, landing_v1_sha256
from src.synthetic.landing import write_landing_tables

PINNED_SHA256 = "f52989ab75f2321393082ea882a2b88c5630b51d22c71ff8af44335092369b72"


def described_columns(path: Path) -> list[tuple[str, str]]:
    rows = duckdb.sql(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
    return [(name, column_type) for name, column_type, *_ in rows]


class TestWriteLandingTables(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_one_walk_row_conforms_to_the_contract(self) -> None:
        write_landing_tables(
            self.directory,
            {
                "walk": [
                    {
                        "source": "syn",
                        "area": "villa-norte-rent",
                        "run_started_at": datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
                        "observed_date": date(2026, 10, 1),
                        "complete": True,
                    }
                ]
            },
        )

        self.assertEqual(
            landing_v1_columns()["walk"], described_columns(self.directory / "walk.parquet")
        )

    def test_absent_columns_are_null_and_keep_their_type(self) -> None:
        write_landing_tables(
            self.directory,
            {"walk": [{"source": "syn", "area": "villa-norte-rent"}]},
        )

        pages, result_count = duckdb.sql(
            f"SELECT pages, result_count FROM read_parquet('{self.directory / 'walk.parquet'}')"
        ).fetchone()
        self.assertIsNone(pages)
        self.assertIsNone(result_count)
        self.assertIn(
            ("result_count", "INTEGER"), described_columns(self.directory / "walk.parquet")
        )

    def test_every_contract_table_is_written_even_without_rows(self) -> None:
        write_landing_tables(self.directory, {})

        for table, columns in landing_v1_columns().items():
            with self.subTest(table=table):
                path = self.directory / f"{table}.parquet"
                self.assertEqual(columns, described_columns(path))
                self.assertEqual(
                    0, duckdb.sql(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
                )

    def test_list_and_timestamp_values_round_trip(self) -> None:
        observed_at = datetime(2026, 10, 1, 23, 59, tzinfo=UTC)
        write_landing_tables(
            self.directory,
            {
                "listing_text": [
                    {
                        "source_listing_id": "syn-1",
                        "observed_at": observed_at,
                        "features": ["Ascensor", "Terraza"],
                    }
                ]
            },
        )

        stored_at, features = duckdb.sql(
            "SELECT observed_at = TIMESTAMPTZ '2026-10-01 23:59:00+00', features "
            f"FROM read_parquet('{self.directory / 'listing_text.parquet'}')"
        ).fetchone()
        self.assertTrue(stored_at)
        self.assertEqual(["Ascensor", "Terraza"], features)

    def test_unknown_table_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "listing_photo"):
            write_landing_tables(self.directory, {"listing_photo": []})

    def test_unknown_column_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "asking_price"):
            write_landing_tables(self.directory, {"observation": [{"asking_price": 1}]})


class TestContractReader(unittest.TestCase):
    def test_digest_is_computed_from_the_contract_file(self) -> None:
        self.assertEqual(PINNED_SHA256, landing_v1_sha256())

    def test_columns_are_listed_per_table_in_order(self) -> None:
        columns = landing_v1_columns()

        self.assertEqual(["walk", "observation", "listing_detail", "listing_text"], list(columns))
        self.assertEqual(("source", "VARCHAR"), columns["observation"][0])
        self.assertIn(("orientation", "VARCHAR[]"), columns["listing_detail"])


if __name__ == "__main__":
    unittest.main()
