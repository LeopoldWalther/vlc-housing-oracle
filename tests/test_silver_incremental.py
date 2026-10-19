"""Incremental Silver through any sequence of landing commits equals a backfill of the final landing."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import AlwaysTrue

from src.contracts import landing_v3_columns
from src.silver.landing import register_landing_v3
from src.silver.rebuild import (
    NAMESPACE,
    SILVER_TABLES,
    build_tables,
    local_commits,
    process_landing,
)
from src.synthetic.landing import generate_landing_v3

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


class TestIncrementalEqualsBackfill(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.landing = Path(cls.temporary.name) / "landing" / "v3"
        cls.landing.mkdir(parents=True)
        generate_landing_v3(cls.landing, seed=1)
        cls.commits = local_commits(cls.landing)
        # The oracle: FEATURE-001's rules over every run at once, with no incremental logic.
        connection = duckdb.connect()
        register_landing_v3(
            connection,
            str(cls.landing),
            dict.fromkeys(landing_v3_columns(), {c.run_id for c in cls.commits}),
            {c.run_id: c.run_started_at for c in cls.commits},
            {},
        )
        cls.oracle = {
            name: sorted(map(repr, table.to_pylist()))
            for name, table in build_tables(connection).items()
        }
        connection.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def catalog(self) -> SqlCatalog:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def process(self, catalog, commits, mode="incremental", connection=None):
        connection = connection or duckdb.connect()
        self.addCleanup(connection.close)
        return process_landing(
            connection,
            catalog,
            landing=str(self.landing),
            mode=mode,
            list_commits=lambda: list(commits),
            now=lambda: NOW,
        )

    def row_sets(self, catalog) -> dict[str, list[str]]:
        tables = (*SILVER_TABLES, "landing_ingest")
        result = {}
        for name in tables:
            rows = catalog.load_table(f"{NAMESPACE}.{name}").scan().to_arrow().to_pylist()
            if name == "landing_ingest":
                rows = [r["run_id"] for r in rows]
            result[name] = sorted(map(repr, rows))
        return result

    def in_steps(self, size: int) -> dict[str, list[str]]:
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        for end in range(size, len(ordered) + size, size):
            self.process(catalog, ordered[:end])
        return self.row_sets(catalog)

    def backfill(self) -> dict[str, list[str]]:
        catalog = self.catalog()
        self.process(catalog, self.commits, mode="backfill")
        return self.row_sets(catalog)

    def test_a_backfill_equals_the_full_rule_over_every_run(self) -> None:
        actual = self.backfill()

        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self.oracle[table], actual[table])

    def test_one_at_a_time_three_at_a_time_and_all_at_once_agree(self) -> None:
        expected = self.backfill()

        for size in (1, 3):
            with self.subTest(commits_per_run=size):
                actual = self.in_steps(size)
                for table in expected:
                    with self.subTest(table=table):
                        self.assertEqual(expected[table], actual[table])
                        if table in self.oracle:
                            self.assertEqual(self.oracle[table], actual[table])
        self.assertEqual(len(self.commits), len(expected["landing_ingest"]))

    def test_a_run_interrupted_before_the_ingest_log_is_repeated_to_the_same_result(self) -> None:
        catalog = self.catalog()
        self.process(catalog, self.commits)
        once = self.row_sets(catalog)
        # A crash after the per-day commits and before the ingest log leaves the runs pending.
        catalog.load_table(f"{NAMESPACE}.landing_ingest").delete(AlwaysTrue())

        result = self.process(catalog, self.commits)

        self.assertEqual(len(self.commits), result.pending)
        self.assertEqual(once, self.row_sets(catalog))

    def test_nothing_pending_writes_nothing(self) -> None:
        catalog = self.catalog()
        self.process(catalog, self.commits)
        snapshots = {
            name: catalog.load_table(f"{NAMESPACE}.{name}").current_snapshot().snapshot_id
            for name in SILVER_TABLES
        }

        result = self.process(catalog, self.commits)

        self.assertEqual(0, result.pending)
        for name, snapshot_id in snapshots.items():
            with self.subTest(table=name):
                self.assertEqual(
                    snapshot_id,
                    catalog.load_table(f"{NAMESPACE}.{name}").current_snapshot().snapshot_id,
                )

    def test_a_non_utc_session_zone_gives_the_same_result(self) -> None:
        catalog = self.catalog()
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'Europe/Madrid'")

        self.process(catalog, self.commits, mode="backfill", connection=connection)

        self.assertEqual(self.backfill(), self.row_sets(catalog))


if __name__ == "__main__":
    unittest.main()
