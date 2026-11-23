"""Incremental Silver through any sequence of landing commits equals a backfill of the final landing."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.expressions import AlwaysTrue

from src.contracts import landing_v3_columns
from src.enrichment import text_labels
from src.enrichment.batches import BatchAnswer, BatchState
from src.enrichment.labeler import LabelResult, TextLabel
from src.reference.census_sections import convert
from src.silver.ingest import ingested_runs
from src.silver.landing import LandingContractError, register_landing_v3
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
    TEXT_LABEL_TABLE,
    TRANSFORMATION_VERSION,
    BackfillRequired,
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


class EndingBatches:
    """Fake message batches whose every batch has ended with a valid answer per text."""

    def __init__(self) -> None:
        self.submitted: dict[str, list[tuple[str, str]]] = {}

    def submit(self, texts: Sequence[tuple[str, str]]) -> str:
        batch_id = f"msgbatch_{len(self.submitted) + 1:02d}"
        self.submitted[batch_id] = list(texts)
        return batch_id

    def state(self, batch_id: str) -> BatchState:
        return BatchState(ended=True, ended_at=NOW, readable=True)

    def answers(self, batch_id: str) -> Iterator[BatchAnswer]:
        for text_sha256, _ in self.submitted[batch_id]:
            yield BatchAnswer(
                text_sha256, "succeeded", LabelResult(TextLabel("renovated", True), 100, 10)
            )


class TestRunsGivenTextLabels(unittest.TestCase):
    """A run given the labeller's stored answers writes listing_text_label beside every table."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.landing = cls.root / "landing" / "v3"
        cls.landing.mkdir(parents=True)
        generate_landing_v3(cls.landing, seed=1)
        cls.commits = sorted(local_commits(cls.landing), key=lambda c: (c.run_started_at, c.run_id))
        # The answers are stored once, as the labeller stores them: a backfill, then one run
        # that submits and one that collects.
        cls.labels = str(cls.root / "labels")
        catalog = SqlCatalog(
            "labeller", uri=f"sqlite:///{cls.root}/labeller.db", warehouse=f"file://{cls.root}"
        )
        with duckdb.connect() as connection:
            process_landing(
                connection,
                catalog,
                landing=str(cls.landing),
                mode="backfill",
                list_commits=lambda: list(cls.commits),
                now=lambda: NOW,
            )
        batches = EndingBatches()
        arguments = [
            "--catalog",
            "labeller",
            "--labels",
            cls.labels,
            "--batches",
            str(cls.root / "batches"),
            "--model-id",
            "model-a",
            "--max-texts",
            "50",
        ]
        with (
            mock.patch("src.enrichment.text_labels.load_catalog", return_value=catalog),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            for _ in range(2):
                if text_labels.main(arguments, batches=batches, now=lambda: NOW) != 0:
                    raise RuntimeError("the labeller fixture failed")

    def catalog(self) -> SqlCatalog:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def process(self, catalog, commits, *, mode="incremental", labels=None):
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        return process_landing(
            connection,
            catalog,
            landing=str(self.landing),
            mode=mode,
            list_commits=lambda: list(commits),
            now=lambda: NOW,
            text_labels=labels,
        )

    def rows(self, catalog, name: str) -> list[dict]:
        return catalog.load_table(f"{NAMESPACE}.{name}").scan().to_arrow().to_pylist()

    def row_sets(self, catalog, tables) -> dict[str, list[str]]:
        return {name: sorted(map(repr, self.rows(catalog, name))) for name in tables}

    def snapshots(self, catalog) -> dict[str, int]:
        return {
            name: catalog.load_table((namespace, name)).current_snapshot().snapshot_id
            for namespace, name in catalog.list_tables(NAMESPACE)
        }

    def test_a_backfill_labels_every_sale_listing_with_a_description(self) -> None:
        catalog = self.catalog()

        result = self.process(catalog, self.commits, mode="backfill", labels=self.labels)

        self.assertIsNotNone(result.snapshot_ids[TEXT_LABEL_TABLE])
        operation = {
            row["source_listing_id"]: row["operation"] for row in self.rows(catalog, "listing")
        }
        texts = self.rows(catalog, "listing_text")
        labelled = {row["source_listing_id"]: row for row in self.rows(catalog, TEXT_LABEL_TABLE)}
        self.assertEqual(len(labelled), len(texts))
        sale_texts = [
            row["source_listing_id"]
            for row in texts
            if operation.get(row["source_listing_id"]) == "sale"
            and (row["description"] or "").strip()
        ]
        self.assertTrue(sale_texts)
        for listing_id in sale_texts:
            with self.subTest(listing=listing_id):
                row = labelled[listing_id]
                self.assertEqual(
                    (row["renovation_state"], row["is_auction"], row["model_id"]),
                    ("renovated", True, "model-a"),
                )

    def test_commits_one_at_a_time_give_the_rows_of_a_backfill(self) -> None:
        tables = (*WRITTEN_TABLES, TEXT_LABEL_TABLE)
        backfilled = self.catalog()
        self.process(backfilled, self.commits, mode="backfill", labels=self.labels)
        stepwise = self.catalog()

        for end in range(1, len(self.commits) + 1):
            self.process(stepwise, self.commits[:end], labels=self.labels)

        expected = self.row_sets(backfilled, tables)
        actual = self.row_sets(stepwise, tables)
        for table in tables:
            with self.subTest(table=table):
                self.assertEqual(expected[table], actual[table])

    def test_an_empty_directory_writes_the_table_with_null_labels(self) -> None:
        catalog = self.catalog()
        empty = Path(self.enterContext(tempfile.TemporaryDirectory()))

        self.process(catalog, self.commits, labels=str(empty))

        rows = self.rows(catalog, TEXT_LABEL_TABLE)
        self.assertEqual(len(rows), len(self.rows(catalog, "listing_text")))
        self.assertEqual({row["renovation_state"] for row in rows}, {None})

    def test_a_run_without_the_argument_writes_no_such_table_and_the_others_as_before(
        self,
    ) -> None:
        with_labels = self.catalog()
        self.process(with_labels, self.commits, labels=self.labels)
        without = self.catalog()

        result = self.process(without, self.commits)

        self.assertFalse(without.table_exists(f"{NAMESPACE}.{TEXT_LABEL_TABLE}"))
        self.assertNotIn(TEXT_LABEL_TABLE, result.snapshot_ids)
        self.assertEqual(
            self.row_sets(without, WRITTEN_TABLES), self.row_sets(with_labels, WRITTEN_TABLES)
        )

    def test_nothing_pending_writes_no_snapshot_of_the_table(self) -> None:
        catalog = self.catalog()
        self.process(catalog, self.commits, labels=self.labels)
        before = self.snapshots(catalog)

        result = self.process(catalog, self.commits, labels=self.labels)

        self.assertEqual(0, result.pending)
        self.assertIn(TEXT_LABEL_TABLE, before)
        self.assertEqual(self.snapshots(catalog), before)


class TestVersionGuard(unittest.TestCase):
    """An incremental run refuses tables that another rule or redaction version wrote."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.landing = Path(cls.temporary.name) / "landing" / "v3"
        cls.landing.mkdir(parents=True)
        generate_landing_v3(cls.landing, seed=1)
        cls.commits = sorted(local_commits(cls.landing), key=lambda c: (c.run_started_at, c.run_id))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def process(self, commits, mode="incremental"):
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        return process_landing(
            connection,
            self.catalog,
            landing=str(self.landing),
            mode=mode,
            list_commits=lambda: list(commits),
            now=lambda: NOW,
        )

    def snapshot_ids(self) -> dict[str, int]:
        return {
            identifier[-1]: self.catalog.load_table(identifier).current_snapshot().snapshot_id
            for identifier in self.catalog.list_tables(NAMESPACE)
        }

    def test_a_run_under_another_rule_version_is_refused_and_writes_nothing(self) -> None:
        self.process(self.commits[:-1])
        before = self.snapshot_ids()

        with (
            mock.patch("src.silver.rebuild.TRANSFORMATION_VERSION", "silver-v2"),
            self.assertRaises(BackfillRequired) as refused,
        ):
            self.process(self.commits)

        message = str(refused.exception)
        for part in ("silver-v1", "silver-v2", "backfill"):
            self.assertIn(part, message)
        self.assertEqual(before, self.snapshot_ids())

    def test_a_backfill_under_the_new_rule_version_clears_the_refusal(self) -> None:
        self.process(self.commits[:-1])

        with mock.patch("src.silver.rebuild.TRANSFORMATION_VERSION", "silver-v2"):
            self.process(self.commits[:-1], mode="backfill")
            result = self.process(self.commits)

        self.assertEqual((self.commits[-1].run_id,), result.processed)

    def test_a_backfill_that_stops_before_its_log_leaves_the_refusal(self) -> None:
        self.process(self.commits[:-1])

        with mock.patch("src.silver.rebuild.TRANSFORMATION_VERSION", "silver-v2"):
            with (
                mock.patch(
                    "src.silver.rebuild.record_ingested", side_effect=RuntimeError("stopped")
                ),
                self.assertRaises(RuntimeError),
            ):
                self.process(self.commits[:-1], mode="backfill")
            with self.assertRaises(BackfillRequired):
                self.process(self.commits)

    def test_a_run_under_another_redaction_version_is_refused_and_writes_nothing(self) -> None:
        self.process(self.commits[:-1])
        before = self.snapshot_ids()

        with (
            mock.patch("src.silver.rebuild.REDACTION_VERSION", "r3"),
            self.assertRaises(BackfillRequired) as refused,
        ):
            self.process(self.commits)

        message = str(refused.exception)
        for part in ("r2", "r3", "backfill"):
            self.assertIn(part, message)
        self.assertEqual(before, self.snapshot_ids())

    def test_listing_text_without_a_recorded_redaction_version_is_refused(self) -> None:
        self.process(self.commits[:-1])
        listing_text = self.catalog.load_table(f"{NAMESPACE}.listing_text")
        listing_text.overwrite(listing_text.scan().to_arrow())

        with self.assertRaises(BackfillRequired) as refused:
            self.process(self.commits)

        self.assertIn("unknown", str(refused.exception))

    def test_a_backfill_under_the_new_redaction_version_clears_the_refusal(self) -> None:
        self.process(self.commits[:-1])

        with mock.patch("src.silver.rebuild.REDACTION_VERSION", "r3"):
            self.process(self.commits[:-1], mode="backfill")
            result = self.process(self.commits)

        self.assertEqual((self.commits[-1].run_id,), result.processed)

    def test_a_commit_that_does_not_name_a_day_of_its_file_is_refused_before_any_write(
        self,
    ) -> None:
        landing = Path(self.enterContext(tempfile.TemporaryDirectory())) / "v3"
        shutil.copytree(self.landing, landing)
        # The commit names the next day instead of its file's, so its file is still opened; a
        # commit that names no day at all is the case of the next test.
        target = next(c for c in self.commits if c.observed_dates["observation"])
        (day,) = target.observed_dates["observation"]
        path = landing / "_commits" / f"{target.run_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["observed_dates"]["observation"] = [(day + timedelta(days=1)).isoformat()]
        path.write_text(json.dumps(raw), encoding="utf-8")

        for mode in ("incremental", "backfill"):
            with self.subTest(mode=mode):
                connection = duckdb.connect()
                self.addCleanup(connection.close)
                with self.assertRaises(LandingContractError) as refused:
                    process_landing(
                        connection,
                        self.catalog,
                        landing=str(landing),
                        mode=mode,
                        list_commits=lambda: local_commits(landing),
                        now=lambda: NOW,
                    )

                message = str(refused.exception)
                for part in ("observation", target.run_id, day.isoformat()):
                    self.assertIn(part, message)
                self.assertEqual([], self.catalog.list_namespaces())

    def test_a_catalog_without_an_ingest_log_is_not_refused(self) -> None:
        with mock.patch("src.silver.rebuild.TRANSFORMATION_VERSION", "silver-v2"):
            result = self.process(self.commits[:1])

        self.assertEqual((self.commits[0].run_id,), result.processed)

    def test_a_catalog_without_listing_text_is_not_refused_for_its_redaction(self) -> None:
        with mock.patch("src.silver.rebuild.REDACTION_VERSION", "r3"):
            result = self.process(self.commits[:1])

        self.assertEqual((self.commits[0].run_id,), result.processed)


if __name__ == "__main__":
    unittest.main()
