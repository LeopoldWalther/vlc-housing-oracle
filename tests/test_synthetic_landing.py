"""Synthetic landing tables are written exactly as the contract types them."""

import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from src.contracts import (
    landing_v1_columns,
    landing_v1_sha256,
    landing_v3_columns,
    landing_v3_sha256,
)
from src.silver.landing import register_landing
from src.synthetic.landing import (
    generate_landing,
    generate_landing_v3,
    write_landing_tables,
    write_landing_v3,
)

PINNED_SHA256 = "f52989ab75f2321393082ea882a2b88c5630b51d22c71ff8af44335092369b72"


def described_columns(path: Path) -> list[tuple[str, str]]:
    rows = duckdb.sql(
        f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning = false)"
    ).fetchall()
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


def v3_files(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*/run_date=*/run=*/part-0.parquet"))


def v3_commits(root: Path) -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((root / "_commits").glob("*.json"))}


def v3_count(root: Path, table: str, where: str = "true") -> int:
    with utc_connection() as connection:
        return connection.sql(
            f"SELECT count(*) FROM read_parquet('{root}/{table}/*/*/part-0.parquet', "
            f"hive_partitioning = false) WHERE {where}"
        ).fetchone()[0]


class TestWriteLandingV3(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_one_run_gets_a_file_per_table_and_a_true_commit(self) -> None:
        write_landing_v3(
            self.root,
            {
                "walk": [
                    {
                        "source": "syn",
                        "run_id": "20261001T060000Z",
                        "area": "alba-sale",
                        "run_started_at": datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
                        "observed_date": date(2026, 10, 1),
                    }
                ]
            },
        )

        for table, columns in landing_v3_columns().items():
            path = (
                self.root
                / table
                / "run_date=2026-10-01"
                / "run=20261001T060000Z"
                / "part-0.parquet"
            )
            with self.subTest(table=table):
                self.assertEqual(columns, described_columns(path))
        commit = v3_commits(self.root)["20261001T060000Z"]
        self.assertEqual(
            ("landing", "v3", landing_v3_sha256()),
            (commit["contract"], commit["version"], commit["schema_sha256"]),
        )
        self.assertEqual(
            {"walk": 1, "observation": 0, "listing_detail": 0, "listing_text": 0},
            commit["row_counts"],
        )
        self.assertEqual(["2026-10-01"], commit["observed_dates"]["walk"])
        self.assertEqual([], commit["observed_dates"]["observation"])
        self.assertEqual("2026-10-01T06:00:00Z", commit["run_started_at"])
        self.assertEqual(("ok", False), (commit["outcome"], commit["is_replay"]))

    def test_a_row_without_run_id_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "observation"):
            write_landing_v3(self.root, {"observation": [{"source": "syn"}]})

    def test_run_metadata_can_be_given(self) -> None:
        write_landing_v3(
            self.root,
            {
                "observation": [
                    {
                        "source": "syn",
                        "run_id": "import-20260930",
                        "observed_date": date(2026, 9, 30),
                    }
                ]
            },
            runs={"import-20260930": {"outcome": "failed", "is_replay": True}},
        )

        commit = v3_commits(self.root)["import-20260930"]
        self.assertEqual(("failed", True), (commit["outcome"], commit["is_replay"]))
        self.assertEqual("2026-09-30T00:00:00Z", commit["run_started_at"])
        self.assertTrue(
            (
                self.root
                / "observation"
                / "run_date=2026-09-30"
                / "run=import-20260930"
                / "part-0.parquet"
            ).is_file()
        )


class TestGenerateLandingV3(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        generate_landing_v3(cls.root, seed=1)
        cls.commits = v3_commits(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_every_file_conforms_and_every_table_has_every_run(self) -> None:
        files = v3_files(self.root)
        for path in files:
            table = path.parts[-4]
            with self.subTest(file=str(path.relative_to(self.root))):
                self.assertEqual(landing_v3_columns()[table], described_columns(path))
        runs_per_table = {
            t: {p.parts[-2].removeprefix("run=") for p in files if p.parts[-4] == t}
            for t in landing_v3_columns()
        }
        for table, runs in runs_per_table.items():
            with self.subTest(table=table):
                self.assertEqual(set(self.commits), runs)

    def test_every_commit_is_true_of_its_files(self) -> None:
        with utc_connection() as connection:
            for run_id, commit in self.commits.items():
                for table in landing_v3_columns():
                    path = next(self.root.glob(f"{table}/run_date=*/run={run_id}/part-0.parquet"))
                    count, days = connection.sql(
                        f"SELECT count(*), list(DISTINCT observed_date::VARCHAR ORDER BY 1) "
                        f"FROM read_parquet('{path}', hive_partitioning = false)"
                    ).fetchone()
                    with self.subTest(run=run_id, table=table):
                        self.assertEqual(commit["row_counts"][table], count)
                        self.assertEqual(
                            commit["observed_dates"][table], [d for d in (days or []) if d]
                        )

    def test_v3_scenarios_are_present(self) -> None:
        cases = {
            "two live runs on one day": ("observation", "source_listing_id = 'syn-two-runs'", 2),
            "a replay re-deriving an old day": (
                "listing_detail",
                "source_listing_id = 'syn-rederived'",
                2,
            ),
            "an import run without reports": ("observation", "run_id = 'import-20260930'", None),
            "a listing whose detail is older than its latest card": (
                "observation",
                "source_listing_id = 'syn-early-detail'",
                2,
            ),
        }
        for case, (table, where, expected) in cases.items():
            with self.subTest(case=case):
                count = v3_count(self.root, table, where)
                self.assertEqual(expected, count) if expected else self.assertGreater(count, 0)
        self.assertEqual(0, v3_count(self.root, "walk", "run_id = 'import-20260930'"))
        self.assertIn("failed", {c["outcome"] for c in self.commits.values()})
        self.assertTrue(any(c["is_replay"] for c in self.commits.values()))
        self.assertTrue(
            any(
                c["row_counts"]["listing_detail"] == 0 and c["row_counts"]["observation"] > 0
                for c in self.commits.values()
            )
        )

    def test_v1_scenarios_survive_in_v3(self) -> None:
        self.assertGreater(
            v3_count(self.root, "walk", "run_started_at::DATE < run_ended_at::DATE"), 0
        )
        self.assertGreater(v3_count(self.root, "observation", "is_empty_card"), 0)
        self.assertGreater(
            v3_count(
                self.root, "listing_text", "regexp_matches(description, '[6789][0-9]{2} [0-9]{3}')"
            ),
            0,
        )

    def test_every_listing_id_is_synthetic_and_the_seed_is_deterministic(self) -> None:
        for table in ("observation", "listing_detail", "listing_text"):
            with self.subTest(table=table):
                self.assertEqual(
                    0, v3_count(self.root, table, "NOT starts_with(source_listing_id, 'syn-')")
                )
        with tempfile.TemporaryDirectory() as again:
            generate_landing_v3(Path(again), seed=1)
            self.assertEqual(self.commits, v3_commits(Path(again)))


if __name__ == "__main__":
    unittest.main()
