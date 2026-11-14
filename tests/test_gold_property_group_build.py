"""The operator builds listing_property_group from one pinned snapshot of Silver's listing."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog

from src.gold.property_group import (
    PROPERTY_GROUP_SCHEMA,
    BuildReport,
    PropertyGroupError,
    build,
)
from src.silver.rebuild import NAMESPACE, local_commits, process_landing
from src.synthetic.landing import generate_landing_v3

SILVER_AT = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
BUILT_AT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
DATASET = Path("listing_property_group") / "part-0.parquet"


class SilverFixture(unittest.TestCase):
    """A SQLite catalog backfilled from the synthetic landing, and an empty output directory."""

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
        self.out = root / "gold" / "v1"
        self.backfill()

    def backfill(self) -> None:
        with duckdb.connect() as connection:
            process_landing(
                connection,
                self.catalog,
                landing=str(self.landing),
                mode="backfill",
                list_commits=lambda: local_commits(self.landing),
                now=lambda: SILVER_AT,
            )

    def build(self) -> BuildReport:
        return build(self.catalog, str(self.out), now=lambda: BUILT_AT)

    def written_rows(self) -> list[dict]:
        return pq.read_table(self.out / DATASET).to_pylist()

    def listing_snapshot_id(self) -> int:
        return self.catalog.load_table(f"{NAMESPACE}.listing").current_snapshot().snapshot_id

    def listings_with_detail(self) -> list[tuple[str, str]]:
        listing = self.catalog.load_table(f"{NAMESPACE}.listing").scan().to_arrow()
        return sorted(
            (row["source"], row["source_listing_id"])
            for row in listing.to_pylist()
            if row["detail_observed_at"] is not None
        )


class BuildTest(SilverFixture):
    def test_the_file_has_the_contract_and_one_row_per_listing_with_a_detail_record(self) -> None:
        self.build()

        written = pq.read_table(self.out / DATASET)
        self.assertEqual(written.schema.remove_metadata(), PROPERTY_GROUP_SCHEMA)
        keys = [(row["source"], row["source_listing_id"]) for row in written.to_pylist()]
        self.assertEqual(keys, self.listings_with_detail())
        self.assertGreater(len(keys), 0)

    def test_rows_record_the_snapshot_read_and_a_later_build_the_newer_one(self) -> None:
        first = self.listing_snapshot_id()
        report = self.build()
        self.assertEqual(report.snapshot_id, first)
        self.assertEqual(
            {row["silver_listing_snapshot_id"] for row in self.written_rows()}, {first}
        )

        self.backfill()
        newer = self.listing_snapshot_id()
        self.assertNotEqual(newer, first)
        self.build()
        self.assertEqual(
            {row["silver_listing_snapshot_id"] for row in self.written_rows()}, {newer}
        )

    def test_two_builds_on_one_snapshot_with_one_clock_write_equal_rows(self) -> None:
        self.build()
        first = self.written_rows()
        self.build()
        self.assertEqual(self.written_rows(), first)

    def test_the_report_counts_listings_and_groups(self) -> None:
        report = self.build()

        # No synthetic listing has coordinates yet, so every listing is its own group.
        listings = len(self.listings_with_detail())
        self.assertEqual(
            report,
            BuildReport(
                listings=listings,
                groups=0,
                surplus=0,
                incomplete_groups=0,
                groups_above_five=0,
                largest_group=1,
                snapshot_id=self.listing_snapshot_id(),
            ),
        )

    def test_a_catalog_without_a_listing_table_is_refused_and_nothing_is_written(self) -> None:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        empty = SqlCatalog(
            "empty",
            uri=f"sqlite:///{self.out.parent}/empty.db",
            warehouse=f"file://{self.out.parent}",
        )

        with self.assertRaisesRegex(PropertyGroupError, f"{NAMESPACE}.listing"):
            build(empty, str(self.out), now=lambda: BUILT_AT)
        self.assertFalse((self.out / DATASET).exists())


if __name__ == "__main__":
    unittest.main()
