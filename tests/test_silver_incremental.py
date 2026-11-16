"""Incremental Silver through any sequence of landing commits equals a backfill of the final landing."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import AlwaysTrue

from src.contracts import landing_v3_columns
from src.silver.landing import register_landing_v3
from src.silver.property_group import (
    PROPERTY_GROUP_SCHEMA,
    PROPERTY_GROUP_TABLE,
    duplicate_pairs,
    property_group_rows,
)
from src.silver.rebuild import (
    NAMESPACE,
    SILVER_TABLES,
    TRANSFORMATION_VERSION,
    build_tables,
    local_commits,
    process_landing,
)
from src.silver.sink import write_silver_tables
from src.synthetic.landing import generate_landing_v3

NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)

# Every table a run writes: those of build_tables and the property groups computed in Python.
WRITTEN_TABLES = (*SILVER_TABLES, PROPERTY_GROUP_TABLE)


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
        tables = build_tables(connection)
        cls.oracle = {name: sorted(map(repr, table.to_pylist())) for name, table in tables.items()}
        groups = property_group_rows(
            tables["listing"],
            duplicate_pairs(connection, tables["listing"]),
            transformation_version=TRANSFORMATION_VERSION,
        )
        cls.oracle[PROPERTY_GROUP_TABLE] = sorted(map(repr, groups.to_pylist()))
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
        tables = (*WRITTEN_TABLES, "landing_ingest")
        result = {}
        for name in tables:
            rows = catalog.load_table(f"{NAMESPACE}.{name}").scan().to_arrow().to_pylist()
            if name == "landing_ingest":
                rows = [r["run_id"] for r in rows]
            result[name] = sorted(map(repr, rows))
        return result

    def property_groups(self, catalog) -> pa.Table:
        return catalog.load_table(f"{NAMESPACE}.{PROPERTY_GROUP_TABLE}").scan().to_arrow()

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

        for table in WRITTEN_TABLES:
            with self.subTest(table=table):
                self.assertEqual(self.oracle[table], actual[table])

    def test_the_synthetic_duplicate_is_the_only_property_group(self) -> None:
        catalog = self.catalog()
        self.process(catalog, self.commits, mode="backfill")

        rows = self.property_groups(catalog).to_pylist()

        self.assertEqual(
            sorted(
                (row["source_listing_id"], row["property_group_id"], row["group_is_complete"])
                for row in rows
                if row["group_size"] > 1
            ),
            [
                ("syn-twin-a", "syn:syn-twin-a", True),
                ("syn-twin-b", "syn:syn-twin-a", True),
            ],
        )
        self.assertEqual(
            [row["group_size"] for row in rows if row["source_listing_id"] == "syn-new-unit"],
            [1],
        )

    def test_a_price_that_returns_is_a_period_of_its_own(self) -> None:
        catalog = self.catalog()
        self.process(catalog, self.commits, mode="backfill")

        rows = catalog.load_table(f"{NAMESPACE}.listing_price").scan().to_arrow().to_pylist()

        self.assertEqual(
            [
                (row["period"], row["price_eur"], row["previous_price_eur"], row["is_current"])
                for row in rows
                if row["source_listing_id"] == "syn-returned"
            ],
            [(1, 400_000, None, False), (2, 380_000, 400_000, False), (3, 400_000, 380_000, True)],
        )

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
            for name in WRITTEN_TABLES
        }

        result = self.process(catalog, self.commits)

        self.assertEqual(0, result.pending)
        for name, snapshot_id in snapshots.items():
            with self.subTest(table=name):
                self.assertEqual(
                    snapshot_id,
                    catalog.load_table(f"{NAMESPACE}.{name}").current_snapshot().snapshot_id,
                )

    def test_tables_that_predate_listing_quality_gain_it_equal_to_a_backfill(self) -> None:
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        self.process(catalog, ordered[: len(ordered) // 2])
        catalog.drop_table(f"{NAMESPACE}.listing_quality")
        schemas = {
            name: catalog.load_table(f"{NAMESPACE}.{name}").schema()
            for name in SILVER_TABLES
            if name != "listing_quality"
        }

        self.process(catalog, ordered)

        self.assertEqual(self.backfill(), self.row_sets(catalog))
        for name, schema in schemas.items():
            with self.subTest(table=name):
                self.assertEqual(schema, catalog.load_table(f"{NAMESPACE}.{name}").schema())

    def test_tables_that_predate_listing_event_gain_it_equal_to_a_backfill(self) -> None:
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        self.process(catalog, ordered[: len(ordered) // 2])
        catalog.drop_table(f"{NAMESPACE}.listing_event")
        schemas = {
            name: catalog.load_table(f"{NAMESPACE}.{name}").schema()
            for name in SILVER_TABLES
            if name != "listing_event"
        }

        self.process(catalog, ordered)

        self.assertEqual(self.backfill(), self.row_sets(catalog))
        for name, schema in schemas.items():
            with self.subTest(table=name):
                self.assertEqual(schema, catalog.load_table(f"{NAMESPACE}.{name}").schema())

    def test_tables_that_predate_listing_price_gain_it_equal_to_a_backfill(self) -> None:
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        self.process(catalog, ordered[: len(ordered) // 2])
        catalog.drop_table(f"{NAMESPACE}.listing_price")
        schemas = {
            name: catalog.load_table(f"{NAMESPACE}.{name}").schema()
            for name in WRITTEN_TABLES
            if name != "listing_price"
        }

        self.process(catalog, ordered)

        self.assertEqual(self.backfill(), self.row_sets(catalog))
        for name, schema in schemas.items():
            with self.subTest(table=name):
                self.assertEqual(schema, catalog.load_table(f"{NAMESPACE}.{name}").schema())

    def test_tables_that_predate_listing_property_group_gain_it_equal_to_a_backfill(self) -> None:
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        self.process(catalog, ordered[: len(ordered) // 2])
        catalog.drop_table(f"{NAMESPACE}.{PROPERTY_GROUP_TABLE}")
        schemas = {
            name: catalog.load_table(f"{NAMESPACE}.{name}").schema() for name in SILVER_TABLES
        }

        self.process(catalog, ordered)

        self.assertEqual(self.backfill(), self.row_sets(catalog))
        for name, schema in schemas.items():
            with self.subTest(table=name):
                self.assertEqual(schema, catalog.load_table(f"{NAMESPACE}.{name}").schema())

    def test_stale_property_group_rows_are_replaced_by_the_next_run(self) -> None:
        # A run can join or split groups of listings it did not touch, so the table is replaced
        # whole: rows that call every listing its own group must not survive the next run.
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        self.process(catalog, ordered[:-1])
        before = self.property_groups(catalog).to_pylist()
        stale = [
            row
            | {
                "property_group_id": f"{row['source']}:{row['source_listing_id']}",
                "group_size": 1,
                "group_is_complete": True,
            }
            for row in before
        ]
        self.assertNotEqual(before, stale)
        write_silver_tables(
            catalog,
            NAMESPACE,
            {PROPERTY_GROUP_TABLE: pa.Table.from_pylist(stale, schema=PROPERTY_GROUP_SCHEMA)},
            {},
        )

        self.process(catalog, ordered)

        self.assertEqual(
            self.backfill()[PROPERTY_GROUP_TABLE], self.row_sets(catalog)[PROPERTY_GROUP_TABLE]
        )

    def test_a_non_utc_session_zone_gives_the_same_result(self) -> None:
        catalog = self.catalog()
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'Europe/Madrid'")

        self.process(catalog, self.commits, mode="backfill", connection=connection)

        self.assertEqual(self.backfill(), self.row_sets(catalog))


if __name__ == "__main__":
    unittest.main()
