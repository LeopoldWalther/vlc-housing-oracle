"""Landing is checked against the contract before Silver reads a single row."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from src.silver.landing import LandingContractError, register_landing_v3
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
AT = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def observation(listing_id: str = "syn-1", **overrides: object) -> dict[str, object]:
    row = {
        "source": "syn",
        "area": "villa-norte-rent",
        "source_listing_id": listing_id,
        "observed_at": AT,
        "observed_date": DAY,
        "is_empty_card": False,
    }
    return row | overrides


def detail_key(listing_id: str = "syn-1") -> dict[str, object]:
    return {
        "source": "syn",
        "area": "villa-norte-rent",
        "source_listing_id": listing_id,
        "observed_at": AT,
        "observed_date": DAY,
    }


class TestRegisterLanding(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.connection = duckdb.connect()
        self.addCleanup(self.connection.close)

    def rewrite(self, table: str, select: str) -> None:
        """Replace one landing file with the result of ``select`` over its current content."""
        path = run_file(self.directory, table)
        duckdb.sql(f"SELECT * FROM read_parquet('{path}', hive_partitioning = false)").to_table(
            f"old_{table}"
        )
        duckdb.sql(f"COPY ({select.format(old=f'old_{table}')}) TO '{path}' (FORMAT parquet)")
        duckdb.sql(f"DROP TABLE old_{table}")

    def assert_rejected(self, *fragments: str) -> LandingContractError:
        with self.assertRaises(LandingContractError) as raised:
            register_one_run(self.connection, self.directory)
        for fragment in fragments:
            self.assertIn(fragment, str(raised.exception))
        return raised.exception

    def test_conforming_landing_registers_four_views(self) -> None:
        write_one_run(
            self.directory,
            {
                "observation": [observation()],
                "listing_detail": [detail_key()],
                "listing_text": [detail_key()],
            },
        )

        register_one_run(self.connection, self.directory)

        counts = {
            view: self.connection.sql(f"SELECT count(*) FROM {view}").fetchone()[0]
            for view in (
                "landing_walk",
                "landing_observation",
                "landing_listing_detail",
                "landing_listing_text",
            )
        }
        self.assertEqual(
            {
                "landing_walk": 0,
                "landing_observation": 1,
                "landing_listing_detail": 1,
                "landing_listing_text": 1,
            },
            counts,
        )

    def test_missing_table_file_is_rejected(self) -> None:
        write_one_run(self.directory, {})
        run_file(self.directory, "walk").unlink()

        self.assert_rejected("walk")

    def test_reordered_column_is_rejected(self) -> None:
        write_one_run(self.directory, {})
        self.rewrite("walk", "SELECT area, source, * EXCLUDE (source, area) FROM {old}")

        self.assert_rejected("walk", "source")

    def test_wrong_column_type_is_rejected(self) -> None:
        write_one_run(self.directory, {})
        self.rewrite("walk", "SELECT * REPLACE (pages::BIGINT AS pages) FROM {old}")

        self.assert_rejected("walk", "pages", "BIGINT")

    def test_null_listing_id_is_rejected_with_a_count(self) -> None:
        write_one_run(
            self.directory,
            {"observation": [observation(), observation(None), observation(None)]},
        )

        self.assert_rejected("observation", "source_listing_id", "2")

    def test_null_empty_card_flag_is_rejected(self) -> None:
        write_one_run(self.directory, {"observation": [observation(is_empty_card=None)]})

        self.assert_rejected("observation", "is_empty_card", "1")

    def test_walk_without_run_instants_is_accepted(self) -> None:
        write_one_run(
            self.directory,
            {"walk": [{"source": "syn", "area": "villa-norte-rent", "observed_date": DAY}]},
        )

        register_one_run(self.connection, self.directory)

    def test_duplicate_observation_key_is_rejected_without_values(self) -> None:
        write_one_run(
            self.directory,
            {
                "observation": [
                    observation("syn-secret-7", price_eur=1000),
                    observation("syn-secret-7", price_eur=900),
                ]
            },
        )

        error = self.assert_rejected("observation", "1")
        self.assertNotIn("syn-secret-7", str(error))

    def test_duplicate_detail_key_is_rejected(self) -> None:
        write_one_run(
            self.directory,
            {
                "listing_detail": [detail_key(), detail_key()],
                "listing_text": [detail_key()],
            },
        )

        self.assert_rejected("listing_detail")

    def test_detail_without_text_is_rejected(self) -> None:
        write_one_run(self.directory, {"listing_detail": [detail_key()]})

        self.assert_rejected("listing_detail", "listing_text", "1")

    def test_text_without_detail_is_rejected(self) -> None:
        write_one_run(self.directory, {"listing_text": [detail_key()]})

        self.assert_rejected("listing_text", "listing_detail", "1")


if __name__ == "__main__":
    unittest.main()
