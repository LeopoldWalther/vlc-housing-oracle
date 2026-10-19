"""The affected listings are recomputed from Silver's history, equal to the full rule over all runs."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog.sql import SqlCatalog

from src.contracts import landing_v3_columns
from src.silver.affected import affected_listings
from src.silver.landing import register_landing_v3
from src.silver.rebuild import NAMESPACE, build_tables, update_listings
from src.silver.sink import replace_days, upsert_rows
from src.synthetic.landing import write_landing_v3

TABLES = tuple(landing_v3_columns())
KEYS = ["source", "source_listing_id"]
PROPERTIES = {"transformation_version": "silver-v1"}
R0, R1, R2 = "20260930T060000Z", "20261001T060000Z", "20261004T060000Z"
STARTED = {
    run: datetime.strptime(run, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) for run in (R0, R1, R2)
}


def at(run_id: str, minutes: int = 30) -> datetime:
    return STARTED[run_id].replace(minute=minutes)


def card(run_id: str, listing_id: str, price_eur: int = 250_000) -> dict:
    observed_at = at(run_id)
    return {
        "source": "syn",
        "run_id": run_id,
        "area": "alba-sale",
        "operation": "sale",
        "municipality": "alba",
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
        "ingested_at": observed_at,
        "price_eur": price_eur,
        "is_empty_card": False,
    }


def detail(run_id: str, listing_id: str, price_eur: int, description: str) -> dict:
    key = {
        "source": "syn",
        "run_id": run_id,
        "area": "alba-sale",
        "source_listing_id": listing_id,
        "observed_at": at(run_id, 40),
        "observed_date": at(run_id, 40).date(),
        "ingested_at": at(run_id, 40),
    }
    return {
        "listing_detail": [
            key | {"operation": "sale", "municipality": "alba", "price_eur": price_eur}
        ],
        "listing_text": [
            key | {"title": "Piso", "description": description, "features": ["80 m²"]}
        ],
    }


def merge(*parts: dict) -> dict:
    merged: dict = {}
    for part in parts:
        for table, rows in part.items():
            merged.setdefault(table, []).extend(rows)
    return merged


def full_build(root: Path, runs: set[str]) -> dict[str, pa.Table]:
    connection = duckdb.connect()
    register_landing_v3(connection, str(root), dict.fromkeys(TABLES, runs), STARTED, {})
    tables = build_tables(connection)
    connection.close()
    return tables


def by_id(table: pa.Table) -> dict[str, dict]:
    return {row["source_listing_id"]: row for row in table.to_pylist()}


class TestUpdateListings(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )
        # Before: R0 holds an unrelated listing; R1 holds the listing whose detail stays the chosen
        # record, and a listing that will stay unaffected.
        write_landing_v3(
            self.root,
            merge(
                {"observation": [card(R0, "syn-elsewhere")]},
                detail(R0, "syn-elsewhere", 100_000, "Lejos."),
                {"observation": [card(R1, "syn-early-detail"), card(R1, "syn-untouched")]},
                detail(R1, "syn-early-detail", 260_000, "Piso del primer run."),
                detail(R1, "syn-untouched", 300_000, "Sin cambios."),
            ),
        )
        before = full_build(self.root, {R0, R1})
        self.write_days(before)
        upsert_rows(self.catalog, NAMESPACE, "listing", before["listing"], KEYS, (), PROPERTIES)
        upsert_rows(
            self.catalog, NAMESPACE, "listing_text", before["listing_text"], KEYS, (), PROPERTIES
        )
        self.before_listing = self.silver("listing")

        # After: R2 sees the first listing again without a detail page, and a new listing whose
        # description holds a phone number.
        write_landing_v3(
            self.root,
            merge(
                {"observation": [card(R2, "syn-early-detail", 255_000), card(R2, "syn-new")]},
                detail(R2, "syn-new", 400_000, "Llame al 600 000 000."),
            ),
        )
        self.after = full_build(self.root, {R0, R1, R2})
        previous = self.silver("observation")
        self.write_days(self.after, only_run=R2)
        new_rows = self.after["observation"].filter(
            pc.equal(self.after["observation"]["run_id"], R2)
        )
        self.affected = affected_listings(previous.slice(0, 0), new_rows)

    def write_days(self, tables: dict[str, pa.Table], only_run: str | None = None) -> None:
        for name in ("observation", "listing_detail"):
            rows = tables[name]
            days = {
                d
                for d, r in zip(
                    rows["observed_date"].to_pylist(), rows["run_id"].to_pylist(), strict=True
                )
                if only_run in (None, r)
            }
            day_rows = rows.filter(
                pc.is_in(rows["observed_date"], pa.array(sorted(days), pa.date32()))
            )
            replace_days(self.catalog, NAMESPACE, name, day_rows, days, PROPERTIES)

    def silver(self, name: str) -> pa.Table:
        return self.catalog.load_table(f"{NAMESPACE}.{name}").scan().to_arrow()

    def update(self, affected=None) -> None:
        connection = duckdb.connect()
        update_listings(
            connection,
            self.catalog,
            landing=str(self.root),
            affected=self.affected if affected is None else affected,
            run_started=STARTED,
            properties=PROPERTIES,
        )
        connection.close()

    def test_affected_listings_equal_the_full_rule_over_all_runs(self) -> None:
        self.update()

        silver, truth = by_id(self.silver("listing")), by_id(self.after["listing"])
        self.assertEqual({("syn", "syn-early-detail"), ("syn", "syn-new")}, self.affected)
        for listing_id in ("syn-early-detail", "syn-new"):
            with self.subTest(listing=listing_id):
                self.assertEqual(truth[listing_id], silver[listing_id])
        self.assertEqual(R1, silver["syn-early-detail"]["run_id"])
        self.assertEqual(2, silver["syn-early-detail"]["days_observed"])

    def test_the_chosen_record_from_an_earlier_run_brings_that_runs_text(self) -> None:
        self.update()

        texts = by_id(self.silver("listing_text"))
        self.assertEqual("Piso del primer run.", texts["syn-early-detail"]["description"])
        self.assertEqual("Llame al [phone].", texts["syn-new"]["description"])
        self.assertEqual("r1", texts["syn-new"]["redaction_version"])

    def test_only_the_chosen_runs_text_files_are_read(self) -> None:
        next(self.root.glob(f"listing_text/*/run={R0}/part-0.parquet")).unlink()

        self.update()

        self.assertIn("syn-new", by_id(self.silver("listing_text")))

    def test_an_unaffected_listing_is_identical_afterwards(self) -> None:
        self.update()

        self.assertEqual(
            by_id(self.before_listing)["syn-untouched"],
            by_id(self.silver("listing"))["syn-untouched"],
        )

    def test_an_affected_listing_with_no_rows_left_is_deleted(self) -> None:
        ghost = self.before_listing.filter(
            pc.equal(self.before_listing["source_listing_id"], "syn-untouched")
        )
        ghost = ghost.set_column(
            ghost.schema.get_field_index("source_listing_id"),
            "source_listing_id",
            pa.array(["syn-ghost"], ghost.schema.field("source_listing_id").type),
        )
        upsert_rows(self.catalog, NAMESPACE, "listing", ghost, KEYS, (), PROPERTIES)

        self.update(self.affected | {("syn", "syn-ghost")})

        self.assertNotIn("syn-ghost", by_id(self.silver("listing")))


if __name__ == "__main__":
    unittest.main()
