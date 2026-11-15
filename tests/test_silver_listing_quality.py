"""listing_quality: one flag per plausibility and location rule, true only on evidence."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

from src.silver.rebuild import NAMESPACE, listing_quality, local_commits, process_landing
from src.synthetic.landing import generate_landing_v3

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

FLAGS = (
    "price_out_of_bounds",
    "sqm_built_below_min",
    "sqm_built_above_max",
    "price_per_sqm_below_min",
    "price_per_sqm_above_max",
    "sqm_usable_above_built",
    "rooms_above_max",
    "bathrooms_above_rooms",
    "year_built_out_of_range",
    "price_drop_above_max",
    "coordinates_missing",
    "far_from_municipality",
)

QUALITY_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        *((name, pa.bool_()) for name in FLAGS),
        ("transformation_version", pa.string()),
    ]
)

KM_PER_DEGREE_OF_LATITUDE = 111.195

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


def north_of(latitude: float, km: float) -> float:
    return latitude + km / KM_PER_DEGREE_OF_LATITUDE


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

        self.assertEqual(set(), {name for name in FLAGS if row[name]})

    def test_the_columns_and_types_are_the_contract_also_without_listings(self) -> None:
        with duckdb.connect() as connection:
            rows = listing_quality(connection, listing())

        self.assertEqual(0, rows.num_rows)
        self.assertEqual(QUALITY_SCHEMA, rows.schema)


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


class YearFlagTest(unittest.TestCase):
    """The upper bound is the UTC year of the detail observation plus one, not the run's clock."""

    def test_the_year_after_the_observation_is_in_range_and_the_one_after_is_not(self) -> None:
        self.assertIs(flag("year_built_out_of_range", year_built=2027), False)
        self.assertIs(flag("year_built_out_of_range", year_built=2028), True)

    def test_a_later_observation_moves_the_upper_bound(self) -> None:
        a_year_later = OBSERVED.replace(year=2027)

        self.assertIs(
            flag("year_built_out_of_range", year_built=2028, detail_observed_at=a_year_later),
            False,
        )

    def test_the_last_minutes_of_a_utc_year_count_as_that_year(self) -> None:
        new_years_eve = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)

        self.assertIs(
            flag("year_built_out_of_range", year_built=2028, detail_observed_at=new_years_eve),
            True,
        )

    def test_the_lower_bound_is_1500(self) -> None:
        self.assertIs(flag("year_built_out_of_range", year_built=1499), True)
        self.assertIs(flag("year_built_out_of_range", year_built=1500), False)

    def test_a_missing_year_is_not_out_of_range(self) -> None:
        self.assertIs(flag("year_built_out_of_range", year_built=None), False)


class LocationFlagTest(unittest.TestCase):
    # Three listings within 100 m of each other; the municipality's centre lies among them.
    CLOSE = (
        {"source_listing_id": "syn-a", "latitude": 39.47},
        {"source_listing_id": "syn-b", "latitude": 39.4705},
        {"source_listing_id": "syn-c", "latitude": 39.4709},
    )

    def far_flags(self, *rows: dict) -> dict[str, bool]:
        return {row["source_listing_id"]: row["far_from_municipality"] for row in quality(*rows)}

    def test_a_listing_without_a_coordinate_is_missing_them_and_not_far(self) -> None:
        for values in ({"latitude": None}, {"longitude": None}):
            with self.subTest(**values):
                rows = quality(*self.CLOSE, {"source_listing_id": "syn-x"} | values)
                row = rows[-1]

                self.assertIs(row["coordinates_missing"], True)
                self.assertIs(row["far_from_municipality"], False)

    def test_a_listing_with_coordinates_is_not_missing_them(self) -> None:
        self.assertIs(flag("coordinates_missing"), False)

    def test_only_the_listing_beyond_ten_km_is_far_and_it_does_not_move_the_centre(self) -> None:
        far = {"source_listing_id": "syn-far", "latitude": north_of(39.47, 11)}

        self.assertEqual(
            {"syn-a": False, "syn-b": False, "syn-c": False, "syn-far": True},
            self.far_flags(*self.CLOSE, far),
        )

    def test_a_listing_nine_km_away_is_not_far(self) -> None:
        near = {"source_listing_id": "syn-near", "latitude": north_of(39.47, 9)}

        self.assertIs(self.far_flags(*self.CLOSE, near)["syn-near"], False)

    def test_every_municipality_has_its_own_centre(self) -> None:
        # 50 km north, another municipality: its own listings are its centre.
        other = [
            {
                "source_listing_id": f"syn-o{n}",
                "municipality": "brisa",
                "latitude": north_of(39.47, 50) + n * 0.0004,
            }
            for n in range(3)
        ]

        self.assertEqual(
            set(), {key for key, far in self.far_flags(*self.CLOSE, *other).items() if far}
        )

    def test_a_listing_without_a_detail_record_does_not_count_towards_the_centre(self) -> None:
        # With the three undetailed listings the centre would move north and syn-a would be far.
        undetailed = [
            {
                "source_listing_id": f"syn-u{n}",
                "detail_observed_at": None,
                "latitude": north_of(39.47, 30),
            }
            for n in range(3)
        ]
        north = {"source_listing_id": "syn-north", "latitude": north_of(39.47, 30)}

        self.assertEqual(
            {"syn-a": False, "syn-b": False, "syn-c": False, "syn-north": True},
            self.far_flags(*self.CLOSE, north, *undetailed),
        )

    def test_a_listing_without_a_municipality_is_not_far(self) -> None:
        lost = {
            "source_listing_id": "syn-lost",
            "municipality": None,
            "latitude": north_of(39.47, 30),
        }

        self.assertIs(self.far_flags(*self.CLOSE, lost)["syn-lost"], False)


class SyntheticLandingTest(unittest.TestCase):
    """The table a backfill of the synthetic landing writes."""

    PLAUSIBILITY_FLAGS = FLAGS[:10]

    @classmethod
    def setUpClass(cls) -> None:
        root = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        landing = root / "landing"
        landing.mkdir()
        generate_landing_v3(landing, seed=1)
        catalog = SqlCatalog("test", uri=f"sqlite:///{root}/catalog.db", warehouse=f"file://{root}")
        with duckdb.connect() as connection:
            process_landing(
                connection,
                catalog,
                landing=str(landing),
                mode="backfill",
                list_commits=lambda: local_commits(landing),
                now=lambda: datetime(2026, 11, 1, tzinfo=UTC),
            )
        rows = catalog.load_table(f"{NAMESPACE}.listing_quality").scan().to_arrow().to_pylist()
        cls.flags = {
            row["source_listing_id"]: {name for name in FLAGS if row[name]} for row in rows
        }

    def test_the_implausible_listing_has_exactly_its_two_plausibility_flags(self) -> None:
        self.assertEqual(
            {"sqm_built_above_max", "year_built_out_of_range", "coordinates_missing"},
            self.flags["syn-implausible"],
        )

    def test_the_listing_far_from_its_municipality_is_the_only_far_one(self) -> None:
        self.assertEqual({"far_from_municipality"}, self.flags["syn-far"])
        self.assertEqual(
            ["syn-far"],
            [key for key, flags in self.flags.items() if "far_from_municipality" in flags],
        )

    def test_no_other_listing_has_a_plausibility_flag(self) -> None:
        self.assertEqual(
            ["syn-implausible"],
            [key for key, flags in self.flags.items() if flags & set(self.PLAUSIBILITY_FLAGS)],
        )


if __name__ == "__main__":
    unittest.main()
