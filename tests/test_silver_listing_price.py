"""listing_price: one row per listing and price period, from cards and detail records."""

import unittest
from datetime import UTC, datetime

import duckdb
import pyarrow as pa

from src.silver.rebuild import listing_prices

OBSERVATION_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("observed_at", pa.timestamp("us", tz="UTC")),
        ("price_eur", pa.int32()),
        ("prev_price_eur", pa.int32()),
        ("is_empty_card", pa.bool_()),
    ]
)

DETAIL_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("observed_at", pa.timestamp("us", tz="UTC")),
        ("price_eur", pa.int32()),
        ("prev_price_eur", pa.int32()),
    ]
)


def at(day: int, hour: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def card(
    listing_id: str,
    observed_at: datetime,
    price: int | None,
    prev: int | None = None,
    *,
    empty: bool = False,
) -> dict:
    return {
        "source": "syn",
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "price_eur": price,
        "prev_price_eur": prev,
        "is_empty_card": empty,
    }


def detail(listing_id: str, observed_at: datetime, price: int | None, prev: int | None = None):
    return {
        "source": "syn",
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "price_eur": price,
        "prev_price_eur": prev,
    }


def prices(cards: list[dict], details: list[dict] = ()) -> list[dict]:
    return listing_prices(
        duckdb.connect(),
        pa.Table.from_pylist(list(cards), schema=OBSERVATION_SCHEMA),
        pa.Table.from_pylist(list(details), schema=DETAIL_SCHEMA),
    ).to_pylist()


def periods(rows: list[dict], listing_id: str) -> list[tuple]:
    return [
        (row["period"], row["price_eur"], row["sightings"], row["is_current"])
        for row in rows
        if row["source_listing_id"] == listing_id
    ]


class TestPeriods(unittest.TestCase):
    def test_one_price_over_three_days_is_one_period(self):
        rows = prices([card("syn-a", at(day), 200_000) for day in (1, 2, 3)])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["period"], 1)
        self.assertEqual(rows[0]["price_eur"], 200_000)
        self.assertEqual(rows[0]["sightings"], 3)
        self.assertEqual(rows[0]["first_observed_at"], at(1))
        self.assertEqual(rows[0]["last_observed_at"], at(3))
        self.assertIs(rows[0]["is_current"], True)

    def test_a_change_starts_a_new_period(self):
        rows = prices([card("syn-a", at(1), 200_000), card("syn-a", at(2), 190_000)])

        self.assertEqual(periods(rows, "syn-a"), [(1, 200_000, 1, False), (2, 190_000, 1, True)])
        self.assertEqual(rows[1]["first_observed_at"], at(2))

    def test_a_return_to_an_earlier_price_is_a_new_period(self):
        rows = prices(
            [
                card("syn-a", at(1), 200_000),
                card("syn-a", at(2), 190_000),
                card("syn-a", at(3), 190_000),
                card("syn-a", at(4), 200_000),
            ]
        )

        self.assertEqual(
            periods(rows, "syn-a"),
            [(1, 200_000, 1, False), (2, 190_000, 2, False), (3, 200_000, 1, True)],
        )

    def test_sightings_without_a_price_are_ignored(self):
        rows = prices(
            [
                card("syn-a", at(1), 200_000),
                card("syn-a", at(2), 150_000, empty=True),
                card("syn-a", at(3), None),
                card("syn-b", at(1), 100_000, empty=True),
                card("syn-b", at(2), None),
            ],
            [detail("syn-a", at(4), None), detail("syn-b", at(3), None)],
        )

        self.assertEqual(periods(rows, "syn-a"), [(1, 200_000, 1, True)])
        self.assertEqual(rows[0]["last_observed_at"], at(1))
        self.assertEqual(periods(rows, "syn-b"), [])

    def test_a_listing_with_a_detail_record_and_no_card_has_a_period(self):
        rows = prices([], [detail("syn-a", at(1), 300_000), detail("syn-a", at(5), 300_000)])

        self.assertEqual(periods(rows, "syn-a"), [(1, 300_000, 2, True)])

    def test_a_card_comes_before_a_detail_record_of_the_same_instant(self):
        rows = prices([card("syn-a", at(1), 260_000)], [detail("syn-a", at(1), 245_000)])

        self.assertEqual(periods(rows, "syn-a"), [(1, 260_000, 1, False), (2, 245_000, 1, True)])

    def test_two_detail_records_of_one_instant_give_the_lower_price_first(self):
        rows = prices([], [detail("syn-a", at(1), 310_000), detail("syn-a", at(1), 305_000)])

        self.assertEqual(periods(rows, "syn-a"), [(1, 305_000, 1, False), (2, 310_000, 1, True)])

    def test_the_last_period_of_each_listing_is_its_only_current_one(self):
        rows = prices(
            [
                card("syn-a", at(1), 200_000),
                card("syn-a", at(2), 210_000),
                card("syn-b", at(1), 90_000),
                card("syn-b", at(3), 95_000),
                card("syn-b", at(5), 90_000),
            ]
        )

        current = [(row["source_listing_id"], row["period"]) for row in rows if row["is_current"]]
        self.assertEqual(current, [("syn-a", 2), ("syn-b", 3)])

    def test_rows_are_ordered_and_carry_the_transformation_version(self):
        rows = prices(
            [
                card("syn-b", at(1), 90_000),
                card("syn-a", at(2), 210_000),
                card("syn-a", at(1), 200_000),
            ]
        )

        self.assertEqual(
            [(row["source_listing_id"], row["period"]) for row in rows],
            [("syn-a", 1), ("syn-a", 2), ("syn-b", 1)],
        )
        self.assertEqual({row["transformation_version"] for row in rows}, {"silver-v1"})


LISTING_PRICE_COLUMNS = [
    "source",
    "source_listing_id",
    "period",
    "price_eur",
    "first_observed_at",
    "last_observed_at",
    "sightings",
    "previous_price_eur",
    "declared_previous_price_eur",
    "is_current",
    "transformation_version",
]


class TestPreviousPrices(unittest.TestCase):
    def test_the_observed_previous_price_is_the_price_of_the_period_before(self):
        rows = prices(
            [
                card("syn-a", at(1), 200_000),
                card("syn-a", at(2), 190_000),
                card("syn-a", at(3), 200_000),
            ]
        )

        self.assertEqual([row["previous_price_eur"] for row in rows], [None, 200_000, 190_000])

    def test_the_declared_previous_price_is_that_of_the_first_card_of_the_period(self):
        rows = prices(
            [
                card("syn-a", at(1), 200_000),
                card("syn-a", at(2), 190_000, 210_000),
                card("syn-a", at(3), 190_000, 200_000),
            ]
        )

        self.assertEqual([row["declared_previous_price_eur"] for row in rows], [None, 210_000])

    def test_the_declared_previous_price_of_a_period_that_starts_with_a_detail_record(self):
        rows = prices(
            [card("syn-a", at(2), 290_000, 280_000)],
            [detail("syn-a", at(1), 290_000, 300_000)],
        )

        self.assertEqual(
            [(row["period"], row["declared_previous_price_eur"]) for row in rows], [(1, 300_000)]
        )

    def test_a_first_sighting_without_a_declared_price_leaves_it_null(self):
        rows = prices([card("syn-a", at(1), 200_000), card("syn-a", at(2), 200_000, 220_000)])

        self.assertIsNone(rows[0]["declared_previous_price_eur"])

    def test_the_table_has_the_eleven_columns_in_order(self):
        table = listing_prices(
            duckdb.connect(),
            pa.Table.from_pylist([card("syn-a", at(1), 200_000)], schema=OBSERVATION_SCHEMA),
            pa.Table.from_pylist([], schema=DETAIL_SCHEMA),
        )

        self.assertEqual(table.column_names, LISTING_PRICE_COLUMNS)


if __name__ == "__main__":
    unittest.main()
