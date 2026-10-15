"""A rebuild turns a whole landing into one pinnable snapshot per Silver table."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from pyiceberg.catalog.sql import SqlCatalog

from src.contracts import landing_v1_sha256
from src.silver.landing import LandingContractError
from src.silver.rebuild import NAMESPACE, SILVER_TABLES, RebuildResult, rebuild
from src.synthetic.landing import generate_landing

STARTED_AT = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)


class TestRebuild(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.landing = root / "landing"
        self.landing.mkdir()
        generate_landing(self.landing, seed=1)
        warehouse = root / "warehouse"
        warehouse.mkdir()
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def run_rebuild(self, connection: duckdb.DuckDBPyConnection | None = None) -> RebuildResult:
        connection = connection or duckdb.connect()
        self.addCleanup(connection.close)
        return rebuild(connection, self.catalog, landing=self.landing, now=lambda: STARTED_AT)

    def scan(self, table: str, snapshot_id: int) -> list[str]:
        rows = (
            self.catalog.load_table(f"{NAMESPACE}.{table}")
            .scan(snapshot_id=snapshot_id)
            .to_arrow()
            .to_pylist()
        )
        return sorted(map(repr, rows))

    def test_row_counts_match_each_committed_snapshot(self) -> None:
        result = self.run_rebuild()

        self.assertEqual(set(SILVER_TABLES), set(result.row_counts))
        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertGreater(result.row_counts[table], 0)
                self.assertEqual(
                    result.row_counts[table], len(self.scan(table, result.snapshot_ids[table]))
                )

    def test_every_snapshot_records_what_built_it(self) -> None:
        result = self.run_rebuild()

        for table, snapshot_id in result.snapshot_ids.items():
            summary = (
                self.catalog.load_table(f"{NAMESPACE}.{table}").snapshot_by_id(snapshot_id).summary
            )
            with self.subTest(table=table):
                self.assertEqual(landing_v1_sha256(), summary["landing_contract_sha256"])
                self.assertEqual("silver-v1", summary["transformation_version"])
                self.assertEqual("r1", summary["redaction_version"])
                self.assertEqual(STARTED_AT.isoformat(), summary["rebuild_started_at"])

    def test_a_non_utc_session_zone_still_rebuilds(self) -> None:
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'Europe/Madrid'")

        result = self.run_rebuild(connection)

        self.assertEqual(set(SILVER_TABLES), set(result.snapshot_ids))

    def test_non_conforming_landing_creates_no_table(self) -> None:
        (self.landing / "listing_text.parquet").unlink()

        with self.assertRaises(LandingContractError):
            self.run_rebuild()

        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertFalse(self.catalog.table_exists(f"{NAMESPACE}.{table}"))

    def test_two_rebuilds_give_equal_rows(self) -> None:
        # Regression guard for the total orders: an ordering that loses a tiebreaker would let the
        # second rebuild keep different rows.
        first = self.run_rebuild()
        second = self.run_rebuild()

        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertNotEqual(first.snapshot_ids[table], second.snapshot_ids[table])
                self.assertEqual(
                    self.scan(table, first.snapshot_ids[table]),
                    self.scan(table, second.snapshot_ids[table]),
                )


if __name__ == "__main__":
    unittest.main()
