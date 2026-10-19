"""The Iceberg sink overwrites whole tables, one pinnable snapshot per write, and refuses drift."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

from src.silver.sink import SilverSchemaError, replace_days, upsert_rows, write_silver_tables

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


DAYS = "SELECT 'syn-' || i AS source_listing_id, DATE '2026-10-01' + (i % 3)::INT AS observed_date, i::INT AS price_eur FROM range(6) t(i)"


def rows_of(table) -> list[tuple]:
    return sorted(
        (r["source_listing_id"], str(r["observed_date"]), r["price_eur"])
        for r in table.scan().to_arrow().to_pylist()
    )


class TestReplaceDays(unittest.TestCase):
    def setUp(self) -> None:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )
        self.identifier = f"{NAMESPACE}.observation"

    def replace(self, select: str, days: set[date]) -> int:
        return replace_days(self.catalog, NAMESPACE, "observation", arrow(select), days, PROPERTIES)

    def test_a_new_table_is_partitioned_by_day(self) -> None:
        self.replace(DAYS, {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)})

        fields = self.catalog.load_table(self.identifier).spec().fields
        self.assertEqual(["observed_date"], [f.name for f in fields])

    def test_only_the_named_days_are_replaced(self) -> None:
        self.replace(DAYS, {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)})

        snapshot = self.replace(
            "SELECT 'syn-x' AS source_listing_id, DATE '2026-10-02' AS observed_date, 999::INT AS price_eur",
            {date(2026, 10, 2)},
        )

        table = self.catalog.load_table(self.identifier)
        rows = rows_of(table)
        self.assertEqual([("syn-x", "2026-10-02", 999)], [r for r in rows if r[1] == "2026-10-02"])
        self.assertEqual(4, len([r for r in rows if r[1] != "2026-10-02"]))
        self.assertEqual(snapshot, table.current_snapshot().snapshot_id)
        self.assertEqual("silver-v1", table.current_snapshot().summary["transformation_version"])

    def test_an_unpartitioned_table_is_evolved_and_keeps_its_snapshots(self) -> None:
        first = write_silver_tables(
            self.catalog, NAMESPACE, {"observation": arrow(DAYS)}, PROPERTIES
        )

        self.replace(DAYS, {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)})

        table = self.catalog.load_table(self.identifier)
        self.assertEqual(["observed_date"], [f.name for f in table.spec().fields])
        self.assertEqual(6, table.scan(snapshot_id=first["observation"]).to_arrow().num_rows)

    def test_rows_outside_the_named_days_are_refused_before_any_commit(self) -> None:
        with self.assertRaisesRegex(ValueError, "2026-10-03"):
            self.replace(DAYS, {date(2026, 10, 1), date(2026, 10, 2)})

        self.assertFalse(self.catalog.table_exists(self.identifier))

    def test_a_lost_column_is_refused(self) -> None:
        self.replace(DAYS, {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)})

        with self.assertRaises(SilverSchemaError):
            self.replace(
                "SELECT 'syn-x' AS source_listing_id, DATE '2026-10-02' AS observed_date",
                {date(2026, 10, 2)},
            )


LISTING = (
    "SELECT * FROM (VALUES ('syn', 'syn-1', 100, ['Ascensor']), ('syn', 'syn-2', 200, ['Terraza']))"
    " t(source, source_listing_id, price_eur, features)"
)


class TestUpsertRows(unittest.TestCase):
    def setUp(self) -> None:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )
        self.keys = ["source", "source_listing_id"]

    def listing(self) -> dict[str, tuple]:
        rows = self.catalog.load_table(f"{NAMESPACE}.listing").scan().to_arrow().to_pylist()
        return {r["source_listing_id"]: (r["price_eur"], r["features"]) for r in rows}

    def upsert(self, select: str, delete_keys: set[tuple[str, str]] = frozenset()) -> int:
        return upsert_rows(
            self.catalog, NAMESPACE, "listing", arrow(select), self.keys, delete_keys, PROPERTIES
        )

    def test_rows_are_inserted_updated_and_deleted(self) -> None:
        self.upsert(LISTING)

        self.upsert(
            "SELECT * FROM (VALUES ('syn', 'syn-1', 90, ['Ascensor', 'Piscina']), ('syn', 'syn-3', 300, ['Garaje']))"
            " t(source, source_listing_id, price_eur, features)",
            delete_keys={("syn", "syn-2")},
        )

        self.assertEqual(
            {"syn-1": (90, ["Ascensor", "Piscina"]), "syn-3": (300, ["Garaje"])}, self.listing()
        )

    def test_an_unchanged_upsert_leaves_rows_identical(self) -> None:
        self.upsert(LISTING)
        before = self.listing()

        self.upsert(LISTING)

        self.assertEqual(before, self.listing())

    def test_a_lost_column_is_refused(self) -> None:
        self.upsert(LISTING)

        with self.assertRaises(SilverSchemaError):
            self.upsert("SELECT 'syn' AS source, 'syn-1' AS source_listing_id, 1::INT AS price_eur")


if __name__ == "__main__":
    unittest.main()
