"""A backfill turns a whole landing v3 into one pinnable snapshot per Silver table."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from pyiceberg.catalog.sql import SqlCatalog

from src.contracts import landing_v3_sha256
from src.silver.landing import LandingContractError
from src.silver.rebuild import (
    NAMESPACE,
    SILVER_TABLES,
    ProcessResult,
    local_commits,
    process_landing,
)
from src.silver.sink import SilverSchemaError, write_silver_tables
from src.synthetic.landing import generate_landing_v3

STARTED_AT = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)


class TestBackfill(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.landing = root / "landing" / "v3"
        self.landing.mkdir(parents=True)
        generate_landing_v3(self.landing, seed=1)
        warehouse = root / "warehouse"
        warehouse.mkdir()
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def run_backfill(self, connection: duckdb.DuckDBPyConnection | None = None) -> ProcessResult:
        connection = connection or duckdb.connect()
        self.addCleanup(connection.close)
        return process_landing(
            connection,
            self.catalog,
            landing=str(self.landing),
            mode="backfill",
            list_commits=lambda: local_commits(self.landing),
            now=lambda: STARTED_AT,
        )

    def scan(self, table: str, snapshot_id: int | None = None) -> list[str]:
        rows = (
            self.catalog.load_table(f"{NAMESPACE}.{table}")
            .scan(snapshot_id=snapshot_id)
            .to_arrow()
            .to_pylist()
        )
        return sorted(map(repr, rows))

    def test_every_table_is_committed_and_its_snapshot_holds_its_rows(self) -> None:
        result = self.run_backfill()

        self.assertEqual(set(SILVER_TABLES), set(result.snapshot_ids))
        for table in SILVER_TABLES:
            with self.subTest(table=table):
                rows = self.scan(table, result.snapshot_ids[table])
                self.assertGreater(len(rows), 0)
                self.assertEqual(self.scan(table), rows)

    def test_every_snapshot_records_what_built_it(self) -> None:
        result = self.run_backfill()

        for table, snapshot_id in result.snapshot_ids.items():
            summary = (
                self.catalog.load_table(f"{NAMESPACE}.{table}").snapshot_by_id(snapshot_id).summary
            )
            with self.subTest(table=table):
                self.assertEqual(landing_v3_sha256(), summary["landing_contract_sha256"])
                self.assertEqual("silver-v1", summary["transformation_version"])
                self.assertEqual("r1", summary["redaction_version"])
                self.assertEqual(STARTED_AT.isoformat(), summary["rebuild_started_at"])
                self.assertEqual(str(result.pending), summary["landing_run_count"])

    def test_a_non_utc_session_zone_still_backfills(self) -> None:
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'Europe/Madrid'")

        result = self.run_backfill(connection)

        self.assertEqual(set(SILVER_TABLES), set(result.snapshot_ids))

    def test_non_conforming_landing_creates_no_table(self) -> None:
        next(self.landing.glob("listing_text/*/run=*/part-0.parquet")).unlink()

        with self.assertRaises(LandingContractError):
            self.run_backfill()

        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertFalse(self.catalog.table_exists(f"{NAMESPACE}.{table}"))

    def test_two_backfills_give_equal_rows(self) -> None:
        # Regression guard for the total orders: an ordering that loses a tiebreaker would let the
        # second backfill keep different rows.
        self.run_backfill()
        first = {table: self.scan(table) for table in SILVER_TABLES}

        self.run_backfill()

        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertEqual(first[table], self.scan(table))

    def feature_001_tables(self) -> None:
        """Leave the catalog as FEATURE-001 left it in AWS: no run_id, no listing_detail, unpartitioned."""
        self.run_backfill()
        old = {}
        for table in ("walk", "observation", "listing", "listing_text"):
            rows = self.catalog.load_table(f"{NAMESPACE}.{table}").scan().to_arrow()
            old[table] = rows.drop_columns([c for c in ("run_id",) if c in rows.schema.names])
        for table in (*SILVER_TABLES, "landing_ingest"):
            self.catalog.drop_table(f"{NAMESPACE}.{table}")
        write_silver_tables(self.catalog, NAMESPACE, old, {"transformation_version": "silver-v1"})

    def test_a_backfill_over_feature_001_tables_adds_the_new_columns(self) -> None:
        self.run_backfill()
        expected = {table: self.scan(table) for table in SILVER_TABLES}
        self.feature_001_tables()
        old_snapshot = (
            self.catalog.load_table(f"{NAMESPACE}.listing").current_snapshot().snapshot_id
        )

        self.run_backfill()

        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertEqual(expected[table], self.scan(table))
        old = (
            self.catalog.load_table(f"{NAMESPACE}.listing")
            .scan(snapshot_id=old_snapshot)
            .to_arrow()
        )
        self.assertNotIn("run_id", old.schema.names)

    def test_an_incremental_run_over_feature_001_tables_is_refused(self) -> None:
        self.feature_001_tables()

        with self.assertRaises(SilverSchemaError):
            connection = duckdb.connect()
            self.addCleanup(connection.close)
            process_landing(
                connection,
                self.catalog,
                landing=str(self.landing),
                mode="incremental",
                list_commits=lambda: local_commits(self.landing),
                now=lambda: STARTED_AT,
            )


if __name__ == "__main__":
    unittest.main()
