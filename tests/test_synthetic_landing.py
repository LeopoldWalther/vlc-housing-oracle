"""Synthetic landing tables are written exactly as the contract types them."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from src.contracts import landing_v1_columns, landing_v1_sha256
from src.silver.landing import register_landing
from src.synthetic.landing import generate_landing, write_landing_tables

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


def utc_connection() -> duckdb.DuckDBPyConnection:
    """Date casts of instants follow the session zone; the landing contract's days are UTC days."""
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    return connection


def count_where(directory: Path, table: str, where: str) -> int:
    path = directory / f"{table}.parquet"
    with utc_connection() as connection:
        return connection.sql(
            f"SELECT count(*) FROM read_parquet('{path}') WHERE {where}"
        ).fetchone()[0]


def row_set(directory: Path) -> dict[str, list[str]]:
    with utc_connection() as connection:
        return {
            table: sorted(
                map(
                    repr,
                    connection.sql(f"SELECT * FROM read_parquet('{directory / table}.parquet')")
                    .to_arrow_table()
                    .to_pylist(),
                )
            )
            for table in landing_v1_columns()
        }


class TestGenerateLanding(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        generate_landing(cls.directory, seed=1)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def scenario(self, table: str, where: str) -> int:
        """Count the rows of ``table`` matching ``where``."""
        return count_where(self.directory, table, where)

    def test_every_table_conforms_and_passes_the_boundary_check(self) -> None:
        for table, columns in landing_v1_columns().items():
            with self.subTest(table=table):
                self.assertEqual(columns, described_columns(self.directory / f"{table}.parquet"))
        register_landing(duckdb.connect(), self.directory)

    def test_replays_recorded_and_inferred(self) -> None:
        for source in ("recorded", "inferred"):
            with self.subTest(is_replay_source=source):
                self.assertTrue(
                    self.scenario(
                        "walk", f"is_replay AND complete AND is_replay_source = '{source}'"
                    )
                )

    def test_a_legacy_walk_without_run_instants(self) -> None:
        self.assertTrue(self.scenario("walk", "run_started_at IS NULL AND run_ended_at IS NULL"))

    def test_a_two_area_run_crossing_midnight(self) -> None:
        walks = self.directory / "walk.parquet"
        self.assertTrue(
            self.scenario(
                "walk",
                "run_started_at::DATE < run_ended_at::DATE AND run_started_at IN "
                f"(SELECT run_started_at FROM read_parquet('{walks}') "
                "GROUP BY run_started_at HAVING count(*) >= 2)",
            )
        )

    def test_padding_cases(self) -> None:
        cases = {
            "lone empty card": (
                "is_empty_card AND source_listing_id NOT IN "
                "(SELECT source_listing_id FROM read_parquet('{path}') WHERE NOT is_empty_card)"
            ),
            "priced and empty copy of one listing-day": (
                "source_listing_id IN (SELECT source_listing_id FROM read_parquet('{path}') "
                "GROUP BY source_listing_id, observed_date "
                "HAVING count(*) = 2 AND bool_or(is_empty_card) AND bool_or(NOT is_empty_card))"
            ),
            "two empty copies only": (
                "source_listing_id IN (SELECT source_listing_id FROM read_parquet('{path}') "
                "GROUP BY source_listing_id, observed_date "
                "HAVING count(*) = 2 AND bool_and(is_empty_card))"
            ),
        }
        path = self.directory / "observation.parquet"
        for case, where in cases.items():
            with self.subTest(case=case):
                self.assertTrue(self.scenario("observation", where.format(path=path)))

    def test_detail_record_cases(self) -> None:
        path = self.directory / "listing_detail.parquet"
        cases = {
            "detail records on two days": (
                "source_listing_id IN (SELECT source_listing_id FROM read_parquet('{path}') "
                "GROUP BY source_listing_id HAVING count(DISTINCT observed_date) = 2)"
            ),
            "two records at one instant in two areas": (
                "source_listing_id IN (SELECT source_listing_id FROM read_parquet('{path}') "
                "GROUP BY source_listing_id, observed_at, ingested_at "
                "HAVING count(DISTINCT area) = 2)"
            ),
        }
        for case, where in cases.items():
            with self.subTest(case=case):
                self.assertTrue(self.scenario("listing_detail", where.format(path=path)))

    def test_an_observation_only_listing(self) -> None:
        details = self.directory / "listing_detail.parquet"
        self.assertTrue(
            self.scenario(
                "observation",
                f"source_listing_id NOT IN "
                f"(SELECT source_listing_id FROM read_parquet('{details}'))",
            )
        )

    def test_descriptions_with_synthetic_personal_data(self) -> None:
        self.assertTrue(
            self.scenario("listing_text", "regexp_matches(description, '[6789][0-9]{2} [0-9]{3}')")
        )
        self.assertTrue(self.scenario("listing_text", "description LIKE '%Sra. %'"))

    def test_every_listing_id_is_synthetic(self) -> None:
        for table in ("observation", "listing_detail", "listing_text"):
            with self.subTest(table=table):
                self.assertFalse(self.scenario(table, "NOT starts_with(source_listing_id, 'syn-')"))

    def test_the_same_seed_gives_the_same_rows(self) -> None:
        with tempfile.TemporaryDirectory() as again:
            generate_landing(Path(again), seed=1)
            self.assertEqual(row_set(self.directory), row_set(Path(again)))

    def test_another_seed_gives_other_filler(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            generate_landing(Path(other), seed=2)
            self.assertNotEqual(
                row_set(self.directory)["observation"], row_set(Path(other))["observation"]
            )


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
