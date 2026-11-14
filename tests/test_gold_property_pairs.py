"""The certain duplicate rule pg1: which two listings are the same property."""

import unittest
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

from src.gold.property_group import duplicate_pairs

LISTING_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("detail_observed_at", pa.timestamp("us", tz="UTC")),
        ("operation", pa.string()),
        ("municipality", pa.string()),
        ("rooms", pa.int32()),
        ("bathrooms", pa.int32()),
        ("sqm_built", pa.int32()),
        ("price_eur", pa.int32()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("floor", pa.string()),
        ("condition", pa.string()),
    ]
)

DEFAULT_LISTING = {
    "source": "syn",
    "source_listing_id": "syn-a",
    "detail_observed_at": datetime(2026, 10, 1, 9, tzinfo=UTC),
    "operation": "sale",
    "municipality": "alba",
    "rooms": 3,
    "bathrooms": 1,
    "sqm_built": 80,
    "price_eur": 200_000,
    "latitude": 39.47,
    "longitude": -0.37,
    "floor": "2",
    "condition": "good",
}


def listing(**overrides) -> dict:
    """One synthetic listing row: the default listing with the given columns replaced."""
    return DEFAULT_LISTING | overrides


def pairs_of(*listings: dict) -> list[tuple[str, str, str, str]]:
    table = pa.Table.from_pylist(list(listings), schema=LISTING_SCHEMA)
    with duckdb.connect() as connection:
        result = duplicate_pairs(connection, table)
    return [
        (row["source_a"], row["source_listing_id_a"], row["source_b"], row["source_listing_id_b"])
        for row in result.to_pylist()
    ]


class CertainPairTest(unittest.TestCase):
    def test_equal_keys_close_in_price_and_place_give_one_pair_smaller_key_first(self) -> None:
        pairs = pairs_of(
            listing(source_listing_id="syn-b", price_eur=204_000, latitude=39.4709),
            listing(source_listing_id="syn-a"),
        )

        self.assertEqual(pairs, [("syn", "syn-a", "syn", "syn-b")])

    def test_a_listing_never_pairs_with_itself_and_a_pair_is_returned_once(self) -> None:
        self.assertEqual(pairs_of(listing()), [])
        self.assertEqual(
            pairs_of(listing(), listing(source_listing_id="syn-b")),
            [("syn", "syn-a", "syn", "syn-b")],
        )


if __name__ == "__main__":
    unittest.main()
