"""Silver listing: one row per listing, attributes from its newest detail record, no free text."""

import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from src.silver.landing import register_landing
from src.silver.rebuild import build_tables
from src.synthetic.landing import write_landing_tables

KEY_COLUMNS = ("source", "area", "source_listing_id", "observed_at", "observed_date")


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def detail(
    listing_id: str,
    municipality: str,
    observed_at: datetime,
    *,
    price_eur: int | None = 1000,
    ingested_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "source": "syn",
        "area": f"{municipality}-sale",
        "operation": "sale",
        "municipality": municipality,
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
        "price_eur": price_eur,
        "ingested_at": ingested_at or observed_at,
    }


def card(listing_id: str, day: int, *, is_empty_card: bool = False) -> dict[str, object]:
    return {
        "source": "syn",
        "area": "alba-sale",
        "operation": "sale",
        "municipality": "alba",
        "source_listing_id": listing_id,
        "observed_at": at(day),
        "observed_date": date(2026, 10, day),
        "price_eur": None if is_empty_card else 1000,
        "is_empty_card": is_empty_card,
    }


def build_listing(
    details: Sequence[dict[str, object]], cards: Sequence[dict[str, object]] = ()
) -> pa.Table:
    texts = [{column: row[column] for column in KEY_COLUMNS} for row in details]
    with tempfile.TemporaryDirectory() as directory:
        write_landing_tables(
            Path(directory),
            {"listing_detail": details, "listing_text": texts, "observation": cards},
        )
        connection = duckdb.connect()
        register_landing(connection, Path(directory))
        listing = build_tables(connection)["listing"]
        connection.close()
    return listing


def silver_listing(
    details: Sequence[dict[str, object]], cards: Sequence[dict[str, object]] = ()
) -> dict[str, dict[str, object]]:
    """Build listing and index it by id, asserting one row per listing."""
    rows = build_listing(details, cards).to_pylist()
    by_id = {row["source_listing_id"]: row for row in rows}
    assert len(by_id) == len(rows), "listing is not unique on source_listing_id"
    return by_id


class TestSilverListing(unittest.TestCase):
    def test_attributes_come_from_the_newest_detail_record(self) -> None:
        listing = silver_listing(
            [
                detail("syn-1", "alba", at(1), price_eur=1000),
                detail("syn-1", "alba", at(5), price_eur=950),
            ]
        )["syn-1"]

        self.assertEqual(950, listing["price_eur"])
        self.assertEqual(at(5), listing["detail_observed_at"])

    def test_a_tie_on_instants_resolves_to_the_first_municipality(self) -> None:
        details = [
            detail("syn-2", "zarza", at(1), price_eur=1),
            detail("syn-2", "alba", at(1), price_eur=2),
        ]

        for ordering in (details, list(reversed(details))):
            with self.subTest(first_in_landing=ordering[0]["municipality"]):
                self.assertEqual("alba-sale", silver_listing(ordering)["syn-2"]["area"])

    def test_seen_columns_count_every_observed_day_including_padding(self) -> None:
        listing = silver_listing(
            [detail("syn-3", "alba", at(1))],
            [card("syn-3", 1), card("syn-3", 2, is_empty_card=True), card("syn-3", 4)],
        )["syn-3"]

        self.assertEqual(
            (date(2026, 10, 1), date(2026, 10, 4), 3),
            (listing["first_seen"], listing["last_seen"], listing["days_observed"]),
        )

    def test_a_listing_without_detail_has_no_attributes_and_no_area(self) -> None:
        listing = silver_listing([], [card("syn-4", 1)])["syn-4"]

        self.assertEqual(date(2026, 10, 1), listing["first_seen"])
        for column in ("price_eur", "area", "operation", "municipality", "detail_observed_at"):
            with self.subTest(column=column):
                self.assertIsNone(listing[column])

    def test_a_listing_without_observations_has_no_seen_columns(self) -> None:
        listing = silver_listing([detail("syn-5", "alba", at(1))])["syn-5"]

        self.assertEqual(1000, listing["price_eur"])
        for column in ("first_seen", "last_seen", "days_observed"):
            with self.subTest(column=column):
                self.assertIsNone(listing[column])

    def test_no_free_text_and_typed_columns(self) -> None:
        listing = build_listing([detail("syn-6", "alba", at(1))])
        schema = listing.schema

        for column in ("title", "description", "location", "features"):
            with self.subTest(column=column):
                self.assertNotIn(column, schema.names)
        self.assertEqual(pa.int32(), schema.field("year_built").type)
        self.assertEqual(pa.int32(), schema.field("days_observed").type)
        self.assertEqual(pa.timestamp("us", tz="UTC"), schema.field("detail_observed_at").type)
        self.assertEqual({"silver-v1"}, set(listing.column("transformation_version").to_pylist()))


if __name__ == "__main__":
    unittest.main()
