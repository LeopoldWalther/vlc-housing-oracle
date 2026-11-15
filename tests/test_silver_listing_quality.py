"""listing_quality: one flag per plausibility and location rule, true only on evidence."""

import unittest
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

from src.silver.rebuild import listing_quality

OBSERVED = datetime(2026, 10, 5, 8, tzinfo=UTC)

LISTING_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("detail_observed_at", pa.timestamp("us", tz="UTC")),
        ("operation", pa.string()),
        ("municipality", pa.string()),
        ("price_eur", pa.int32()),
        ("prev_price_eur", pa.int32()),
        ("sqm_built", pa.int32()),
        ("sqm_usable", pa.int32()),
        ("rooms", pa.int32()),
        ("bathrooms", pa.int32()),
        ("year_built", pa.int32()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
    ]
)

# A plausible flat for sale: no rule is broken.
PLAUSIBLE = {
    "source": "syn",
    "source_listing_id": "syn-1",
    "detail_observed_at": OBSERVED,
    "operation": "sale",
    "municipality": "alba",
    "price_eur": 250_000,
    "prev_price_eur": None,
    "sqm_built": 80,
    "sqm_usable": 70,
    "rooms": 3,
    "bathrooms": 1,
    "year_built": 1975,
    "latitude": 39.47,
    "longitude": -0.37,
}


def listing(*rows: dict) -> pa.Table:
    return pa.Table.from_pylist([PLAUSIBLE | row for row in rows], schema=LISTING_SCHEMA)


def quality(*rows: dict) -> list[dict]:
    with duckdb.connect() as connection:
        return listing_quality(connection, listing(*rows)).to_pylist()


def flag(name: str, **values: object) -> object:
    """The named flag of one listing that differs from the plausible flat in ``values``."""
    (row,) = quality(values)
    return row[name]


class RowsTest(unittest.TestCase):
    def test_one_row_per_listing_with_a_detail_record_ordered_by_key(self) -> None:
        rows = quality(
            {"source_listing_id": "syn-b"},
            {"source_listing_id": "syn-c", "detail_observed_at": None},
            {"source_listing_id": "syn-a"},
        )

        self.assertEqual(
            [("syn", "syn-a"), ("syn", "syn-b")],
            [(row["source"], row["source_listing_id"]) for row in rows],
        )

    def test_a_row_carries_the_transformation_version(self) -> None:
        (row,) = quality({})

        self.assertEqual("silver-v1", row["transformation_version"])

    def test_a_plausible_listing_has_no_flag(self) -> None:
        (row,) = quality({})

        flags = {name: value for name, value in row.items() if isinstance(value, bool)}
        self.assertEqual(9, len(flags))
        self.assertEqual(set(), {name for name, value in flags.items() if value})


class PlausibilityFlagTest(unittest.TestCase):
    # (flag, the listing's values, expected): just over the threshold, exactly at it, and with a
    # missing input, for sale and for rent where their bounds differ.
    CASES = (
        ("price_out_of_bounds", {"price_eur": 9_999, "sqm_built": 20}, True),
        ("price_out_of_bounds", {"price_eur": 10_000, "sqm_built": 20}, False),
        ("price_out_of_bounds", {"price_eur": 20_000_001, "sqm_built": 1_500}, True),
        ("price_out_of_bounds", {"price_eur": 20_000_000, "sqm_built": 1_500}, False),
        ("price_out_of_bounds", {"operation": "rent", "price_eur": 99}, True),
        ("price_out_of_bounds", {"operation": "rent", "price_eur": 100}, False),
        ("price_out_of_bounds", {"operation": "rent", "price_eur": 30_001}, True),
        ("price_out_of_bounds", {"operation": "rent", "price_eur": 30_000}, False),
        ("price_out_of_bounds", {"price_eur": None}, False),
        ("sqm_built_below_min", {"sqm_built": 9}, True),
        ("sqm_built_below_min", {"sqm_built": 10}, False),
        ("sqm_built_below_min", {"sqm_built": None}, False),
        ("sqm_built_above_max", {"sqm_built": 2_001}, True),
        ("sqm_built_above_max", {"sqm_built": 2_000}, False),
        ("sqm_built_above_max", {"sqm_built": None}, False),
        ("price_per_sqm_below_min", {"price_eur": 29_999, "sqm_built": 100}, True),
        ("price_per_sqm_below_min", {"price_eur": 30_000, "sqm_built": 100}, False),
        (
            "price_per_sqm_below_min",
            {"operation": "rent", "price_eur": 199, "sqm_built": 100},
            True,
        ),
        (
            "price_per_sqm_below_min",
            {"operation": "rent", "price_eur": 200, "sqm_built": 100},
            False,
        ),
        ("price_per_sqm_below_min", {"sqm_built": None}, False),
        ("price_per_sqm_below_min", {"price_eur": None}, False),
        ("price_per_sqm_below_min", {"sqm_built": 0}, False),
        ("price_per_sqm_above_max", {"price_eur": 1_500_001, "sqm_built": 100}, True),
        ("price_per_sqm_above_max", {"price_eur": 1_500_000, "sqm_built": 100}, False),
        (
            "price_per_sqm_above_max",
            {"operation": "rent", "price_eur": 6_001, "sqm_built": 100},
            True,
        ),
        (
            "price_per_sqm_above_max",
            {"operation": "rent", "price_eur": 6_000, "sqm_built": 100},
            False,
        ),
        ("price_per_sqm_above_max", {"sqm_built": None}, False),
        ("price_per_sqm_above_max", {"sqm_built": 0}, False),
        ("sqm_usable_above_built", {"sqm_usable": 81}, True),
        ("sqm_usable_above_built", {"sqm_usable": 80}, False),
        ("sqm_usable_above_built", {"sqm_usable": None}, False),
        ("rooms_above_max", {"rooms": 13, "bathrooms": 2}, True),
        ("rooms_above_max", {"rooms": 12, "bathrooms": 2}, False),
        ("rooms_above_max", {"rooms": None}, False),
        ("bathrooms_above_rooms", {"rooms": 3, "bathrooms": 6}, True),
        ("bathrooms_above_rooms", {"rooms": 3, "bathrooms": 5}, False),
        ("bathrooms_above_rooms", {"rooms": None, "bathrooms": 6}, False),
        ("bathrooms_above_rooms", {"bathrooms": None}, False),
        ("price_drop_above_max", {"price_eur": 99_999, "prev_price_eur": 200_000}, True),
        ("price_drop_above_max", {"price_eur": 100_000, "prev_price_eur": 200_000}, False),
        ("price_drop_above_max", {"prev_price_eur": None}, False),
    )

    def test_a_flag_is_true_just_over_its_threshold_and_false_at_it_or_without_input(self) -> None:
        for name, values, expected in self.CASES:
            with self.subTest(flag=name, **values):
                self.assertIs(flag(name, **values), expected)

    def test_another_operation_is_judged_at_the_sale_bounds(self) -> None:
        self.assertIs(flag("price_out_of_bounds", operation="auction", price_eur=9_999), True)
        self.assertIs(flag("price_out_of_bounds", operation="auction", price_eur=10_000), False)

    def test_extreme_values_are_flagged_and_do_not_overflow(self) -> None:
        (row,) = quality(
            {"sqm_built": 300_000, "price_eur": 2_000_000_000, "prev_price_eur": 2_100_000_000}
        )

        self.assertIs(row["sqm_built_above_max"], True)
        self.assertIs(row["price_out_of_bounds"], True)
        self.assertIs(row["price_per_sqm_above_max"], False)
        self.assertIs(row["price_drop_above_max"], False)


if __name__ == "__main__":
    unittest.main()
