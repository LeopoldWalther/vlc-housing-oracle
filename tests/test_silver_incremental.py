"""Incremental Silver through any sequence of landing commits equals a backfill of the final landing."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import AlwaysTrue

from src.contracts import landing_v3_columns
from src.reference.census_sections import convert
from src.silver.ingest import ingested_runs
from src.silver.landing import register_landing_v3
from src.silver.property_group import (
    PROPERTY_GROUP_SCHEMA,
    PROPERTY_GROUP_TABLE,
    duplicate_pairs,
    property_group_rows,
)
from src.silver.rebuild import (
    LOCATION_TABLE,
    NAMESPACE,
    SILVER_TABLES,
    TRANSFORMATION_VERSION,
    ReferenceDataError,
    build_tables,
    local_commits,
    process_landing,
)
from src.silver.sink import write_silver_tables
from src.synthetic.landing import generate_landing_v3
from src.synthetic.reference import PROVINCE, SYNTHETIC_CRS, write_census_archive

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

    def test_the_synthetic_features_give_their_labels_and_one_unknown_wording(self) -> None:
        catalog = self.catalog()
        result = self.process(catalog, self.commits, mode="backfill")

        labels = {
            row["source_listing_id"]: row
            for row in catalog.load_table(f"{NAMESPACE}.listing_feature_label")
            .scan()
            .to_arrow()
            .to_pylist()
        }
        patterns = catalog.load_table(f"{NAMESPACE}.feature_pattern").scan().to_arrow().to_pylist()

        self.assertEqual(labels["syn-furnished"]["furnishing"], "furnished")
        self.assertFalse(labels["syn-furnished"]["is_tenant_occupied"])
        self.assertTrue(labels["syn-tenant"]["is_tenant_occupied"])
        self.assertIsNone(labels["syn-tenant"]["furnishing"])
        self.assertEqual(
            [(row["pattern"], row["listings"]) for row in patterns if row["target"] is None],
            [("vistas al mar desde # miradores", 1)],
        )
        self.assertEqual(result.unknown_feature_patterns, 1)

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

    def test_tables_that_predate_the_feature_tables_gain_them_equal_to_a_backfill(self) -> None:
        feature_tables = ("feature_pattern", "listing_feature_label")
        catalog = self.catalog()
        ordered = sorted(self.commits, key=lambda c: (c.run_started_at, c.run_id))
        self.process(catalog, ordered[: len(ordered) // 2])
        for name in feature_tables:
            catalog.drop_table(f"{NAMESPACE}.{name}")
        schemas = {
            name: catalog.load_table(f"{NAMESPACE}.{name}").schema()
            for name in WRITTEN_TABLES
            if name not in feature_tables
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


class TestRunsGivenCensusSections(unittest.TestCase):
    """A run given a census-section vintage writes listing_location beside every other table."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.landing = cls.root / "landing" / "v3"
        cls.landing.mkdir(parents=True)
        generate_landing_v3(cls.landing, seed=1)
        cls.commits = sorted(local_commits(cls.landing), key=lambda c: (c.run_started_at, c.run_id))
        write_census_archive(cls.root / "sections.zip")
        cls.vintage = cls.converted("2026-01-01")
        cls.later_vintage = cls.converted("2027-01-01")

    @classmethod
    def converted(cls, vintage: str) -> str:
        directory = convert(
            cls.root / "sections.zip",
            province=PROVINCE,
            crs=SYNTHETIC_CRS,
            vintage=vintage,
            source="https://example.org/sections.zip",
            licence="invented",
            retrieved="2026-03-20",
            out=cls.root / "reference",
        )
        return str(directory)

    def catalog(self) -> SqlCatalog:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def process(self, catalog, commits, *, mode="incremental", census_sections=None):
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        return process_landing(
            connection,
            catalog,
            landing=str(self.landing),
            mode=mode,
            list_commits=lambda: list(commits),
            now=lambda: NOW,
            census_sections=census_sections,
        )

    def rows(self, catalog, name: str) -> list[dict]:
        return catalog.load_table(f"{NAMESPACE}.{name}").scan().to_arrow().to_pylist()

    def row_sets(self, catalog, tables) -> dict[str, list[str]]:
        return {name: sorted(map(repr, self.rows(catalog, name))) for name in tables}

    def snapshots(self, catalog) -> dict[str, int]:
        if (NAMESPACE,) not in catalog.list_namespaces():
            return {}
        return {
            name: catalog.load_table((namespace, name)).current_snapshot().snapshot_id
            for namespace, name in catalog.list_tables(NAMESPACE)
        }

    def codes(self, catalog) -> dict[str, tuple]:
        return {
            row["source_listing_id"]: (
                row["municipality_code"],
                row["district_code"],
                row["section_code"],
            )
            for row in self.rows(catalog, LOCATION_TABLE)
        }

    def variant_of_the_vintage(self, name: str, change) -> str:
        """A vintage directory whose file is the good one after ``change``."""
        directory = Path(self.enterContext(tempfile.TemporaryDirectory())) / name
        directory.mkdir()
        good = pq.read_table(Path(self.vintage) / "part-0.parquet")
        pq.write_table(change(good), directory / "part-0.parquet")
        return str(directory)

    def test_a_backfill_locates_the_listings_near_the_alba_point_and_not_the_far_one(
        self,
    ) -> None:
        catalog = self.catalog()

        result = self.process(catalog, self.commits, mode="backfill", census_sections=self.vintage)

        codes = self.codes(catalog)
        for listing_id in ("syn-twin-a", "syn-twin-b", "syn-new-unit"):
            with self.subTest(listing=listing_id):
                self.assertEqual(codes[listing_id], ("99001", "9900101", "9900101001"))
        self.assertEqual(codes["syn-far"], (None, None, None))
        self.assertIsNotNone(result.snapshot_ids[LOCATION_TABLE])

    def test_every_listing_with_a_detail_record_has_one_row_with_the_vintage(self) -> None:
        catalog = self.catalog()

        self.process(catalog, self.commits, mode="backfill", census_sections=self.vintage)

        located = self.rows(catalog, LOCATION_TABLE)
        with_detail = sorted(
            (row["source"], row["source_listing_id"])
            for row in self.rows(catalog, "listing")
            if row["detail_observed_at"] is not None
        )
        self.assertEqual(
            [(row["source"], row["source_listing_id"]) for row in located], with_detail
        )
        self.assertEqual({row["reference_vintage"] for row in located}, {"2026-01-01"})
        self.assertEqual(
            {row["transformation_version"] for row in located}, {TRANSFORMATION_VERSION}
        )

    def test_commits_one_at_a_time_give_the_rows_of_a_backfill(self) -> None:
        tables = (*WRITTEN_TABLES, LOCATION_TABLE)
        backfilled = self.catalog()
        self.process(backfilled, self.commits, mode="backfill", census_sections=self.vintage)
        stepwise = self.catalog()

        for end in range(1, len(self.commits) + 1):
            self.process(stepwise, self.commits[:end], census_sections=self.vintage)

        expected = self.row_sets(backfilled, tables)
        actual = self.row_sets(stepwise, tables)
        for table in tables:
            with self.subTest(table=table):
                self.assertEqual(expected[table], actual[table])

    def test_a_run_without_the_argument_writes_no_such_table_and_the_others_as_before(
        self,
    ) -> None:
        with_sections = self.catalog()
        self.process(with_sections, self.commits, census_sections=self.vintage)
        without = self.catalog()

        result = self.process(without, self.commits)

        self.assertFalse(without.table_exists(f"{NAMESPACE}.{LOCATION_TABLE}"))
        self.assertNotIn(LOCATION_TABLE, result.snapshot_ids)
        self.assertEqual(
            self.row_sets(without, WRITTEN_TABLES), self.row_sets(with_sections, WRITTEN_TABLES)
        )

    def test_a_later_vintage_changes_the_vintage_of_every_row_and_no_code(self) -> None:
        catalog = self.catalog()
        half = len(self.commits) // 2
        self.process(catalog, self.commits[:half], census_sections=self.vintage)
        first = self.catalog()
        self.process(first, self.commits, census_sections=self.vintage)

        self.process(catalog, self.commits, census_sections=self.later_vintage)

        self.assertEqual(
            {row["reference_vintage"] for row in self.rows(catalog, LOCATION_TABLE)},
            {"2027-01-01"},
        )
        self.assertEqual(self.codes(catalog), self.codes(first))

    def test_a_vintage_that_fails_its_check_stops_the_run_before_any_write(self) -> None:
        unreadable = {
            "a missing directory": str(self.root / "reference" / "no-such-vintage"),
            "a file without a column": self.variant_of_the_vintage(
                "no-district", lambda good: good.drop_columns(["district_code"])
            ),
            "a file with two vintages": self.variant_of_the_vintage(
                "two-vintages",
                lambda good: good.set_column(
                    good.schema.get_field_index("vintage"),
                    "vintage",
                    pa.array(["2026-01-01", "2026-01-01", "2027-01-01"]),
                ),
            ),
            "a file with a row that has no vintage": self.variant_of_the_vintage(
                "null-vintage",
                lambda good: good.set_column(
                    good.schema.get_field_index("vintage"),
                    "vintage",
                    pa.array(["2026-01-01", None, "2026-01-01"]),
                ),
            ),
            "a file without rows": self.variant_of_the_vintage(
                "no-rows", lambda good: good.slice(0, 0)
            ),
        }
        half = len(self.commits) // 2
        for case, path in unreadable.items():
            for already_processed in (half, 0):
                with self.subTest(case=case, already_processed=already_processed):
                    catalog = self.catalog()
                    if already_processed:
                        self.process(
                            catalog,
                            self.commits[:already_processed],
                            census_sections=self.vintage,
                        )
                    before = self.snapshots(catalog)

                    with self.assertRaises(ReferenceDataError) as refused:
                        self.process(catalog, self.commits, census_sections=path)

                    self.assertIn(path, str(refused.exception))
                    self.assertNotIn("2027-01-01", str(refused.exception))
                    self.assertNotIn("99001", str(refused.exception))
                    self.assertEqual(self.snapshots(catalog), before)
                    self.assertEqual(
                        set(ingested_runs(catalog, NAMESPACE)),
                        {commit.run_id for commit in self.commits[:already_processed]},
                    )

    def test_the_refusal_names_what_is_wrong(self) -> None:
        catalog = self.catalog()
        without_district = self.variant_of_the_vintage(
            "no-district", lambda good: good.drop_columns(["district_code"])
        )

        with self.assertRaises(ReferenceDataError) as refused:
            self.process(catalog, self.commits, census_sections=without_district)

        self.assertIn("district_code", str(refused.exception))

    def test_nothing_pending_writes_no_snapshot_of_the_table(self) -> None:
        catalog = self.catalog()
        self.process(catalog, self.commits, census_sections=self.vintage)
        before = self.snapshots(catalog)

        result = self.process(catalog, self.commits, census_sections=self.vintage)

        self.assertEqual(0, result.pending)
        self.assertIn(LOCATION_TABLE, before)
        self.assertEqual(self.snapshots(catalog), before)


if __name__ == "__main__":
    unittest.main()
