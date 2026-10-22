"""A replaced day affects every listing it held before and every listing it holds now."""

import unittest

import pyarrow as pa

from src.silver.affected import affected_listings


def rows(*listing_ids: str, source: str = "syn") -> pa.Table:
    return pa.table(
        {
            "source": [source] * len(listing_ids),
            "source_listing_id": list(listing_ids),
            "price_eur": [1] * len(listing_ids),
        }
    )


class TestAffectedListings(unittest.TestCase):
    def test_it_is_the_union_of_the_previous_and_the_new_rows(self) -> None:
        self.assertEqual(
            {("syn", "syn-1"), ("syn", "syn-2"), ("syn", "syn-3")},
            affected_listings(rows("syn-1", "syn-2"), rows("syn-2", "syn-3")),
        )

    def test_a_listing_gone_from_a_replaced_day_is_affected(self) -> None:
        self.assertEqual({("syn", "syn-gone")}, affected_listings(rows("syn-gone"), rows()))

    def test_the_source_is_part_of_the_key(self) -> None:
        self.assertEqual(
            {("syn", "syn-1"), ("other", "syn-1")},
            affected_listings(rows("syn-1"), rows("syn-1", source="other")),
        )


if __name__ == "__main__":
    unittest.main()
