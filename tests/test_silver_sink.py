"""The Iceberg sink overwrites whole tables, one pinnable snapshot per write, and refuses drift."""

import tempfile
import unittest
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

from src.silver.sink import SilverSchemaError, write_silver_tables

NAMESPACE = "silver_test"
PROPERTIES = {"transformation_version": "silver-v1", "rebuild_started_at": "2026-10-01T06:00:00Z"}


def arrow(select: str) -> pa.Table:
    """Build input the way the rebuild does: from a DuckDB result, whose list child is named 'l'."""
    return duckdb.sql(select).to_arrow_table()


FIRST = (
    "SELECT * FROM (VALUES ('syn-1', 100, ['Ascensor'])) t(source_listing_id, price_eur, features)"
)
SECOND = (
    "SELECT * FROM (VALUES ('syn-1', 90, ['Ascensor']), ('syn-2', 200, ['Terraza'])) "
    "t(source_listing_id, price_eur, features)"
)


class TestWriteSilverTables(unittest.TestCase):
    def setUp(self) -> None:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def write(self, select: str) -> dict[str, int]:
        return write_silver_tables(self.catalog, NAMESPACE, {"listing": arrow(select)}, PROPERTIES)

    def current_snapshot_id(self) -> int:
        return self.catalog.load_table(f"{NAMESPACE}.listing").current_snapshot().snapshot_id

    def test_first_write_creates_the_table_and_returns_its_snapshot(self) -> None:
        snapshot_ids = self.write(FIRST)

        self.assertEqual({"listing": self.current_snapshot_id()}, snapshot_ids)
        rows = self.catalog.load_table(f"{NAMESPACE}.listing").scan().to_arrow().to_pylist()
        self.assertEqual(
            [{"source_listing_id": "syn-1", "price_eur": 100, "features": ["Ascensor"]}], rows
        )

    def test_second_write_overwrites_and_the_first_snapshot_stays_readable(self) -> None:
        first = self.write(FIRST)["listing"]
        second = self.write(SECOND)["listing"]

        table = self.catalog.load_table(f"{NAMESPACE}.listing")
        self.assertEqual(second, self.current_snapshot_id())
        self.assertEqual(2, table.scan().to_arrow().num_rows)
        self.assertEqual(
            [{"source_listing_id": "syn-1", "price_eur": 100, "features": ["Ascensor"]}],
            table.scan(snapshot_id=first).to_arrow().to_pylist(),
        )

    def test_every_commit_carries_the_properties(self) -> None:
        first = self.write(FIRST)["listing"]
        second = self.write(SECOND)["listing"]

        table = self.catalog.load_table(f"{NAMESPACE}.listing")
        for snapshot_id in (first, second):
            summary = table.snapshot_by_id(snapshot_id).summary
            with self.subTest(snapshot_id=snapshot_id):
                for key, value in PROPERTIES.items():
                    self.assertEqual(value, summary[key])

    def test_schema_drift_is_refused_and_leaves_the_table_unchanged(self) -> None:
        drifted = {
            "missing column": "SELECT 'syn-1' AS source_listing_id, ['x'] AS features",
            "extra column": FIRST.replace("SELECT *", "SELECT *, 1 AS rooms"),
            "changed type": (
                "SELECT 'syn-1' AS source_listing_id, 100::BIGINT AS price_eur, ['x'] AS features"
            ),
        }
        self.write(FIRST)
        before = self.current_snapshot_id()

        for case, select in drifted.items():
            with self.subTest(case=case):
                with self.assertRaises(SilverSchemaError):
                    self.write(select)
                self.assertEqual(before, self.current_snapshot_id())


if __name__ == "__main__":
    unittest.main()
