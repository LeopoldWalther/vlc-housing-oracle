"""Property groups: connected components of the pairs, and the dataset's rows."""

import itertools
import unittest
from datetime import UTC, datetime

import pyarrow as pa

from src.gold.property_group import group_of, property_group_rows

A, B, C, D, E = (("syn", f"syn-{name}") for name in "abcde")
F, G, H = (("syn", f"syn-{name}") for name in "fgh")

# The dataset's contract (feature plan, Design): column names, types and order.
CONTRACT = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("property_group_id", pa.string()),
        ("group_size", pa.int32()),
        ("group_is_complete", pa.bool_()),
        ("property_group_version", pa.string()),
        ("silver_listing_snapshot_id", pa.int64()),
        ("built_at", pa.timestamp("us", tz="UTC")),
    ]
)
SNAPSHOT_ID = 4242
BUILT_AT = datetime(2026, 11, 14, 12, 30, tzinfo=UTC)


def pairs_table(*pairs: tuple[tuple[str, str], tuple[str, str]]) -> pa.Table:
    return pa.table(
        {
            "source_a": pa.array([a[0] for a, _ in pairs], pa.string()),
            "source_listing_id_a": pa.array([a[1] for a, _ in pairs], pa.string()),
            "source_b": pa.array([b[0] for _, b in pairs], pa.string()),
            "source_listing_id_b": pa.array([b[1] for _, b in pairs], pa.string()),
        }
    )


class GroupOfTest(unittest.TestCase):
    def test_a_chain_is_one_group_named_by_its_smallest_key(self) -> None:
        self.assertEqual(group_of(pairs_table((A, B), (B, C))), {A: A, B: A, C: A})

    def test_separate_pairs_are_separate_groups_and_no_pairs_no_groups(self) -> None:
        self.assertEqual(group_of(pairs_table((A, B), (D, E))), {A: A, B: A, D: D, E: D})
        self.assertEqual(group_of(pairs_table()), {})

    def test_the_order_of_pairs_and_inside_a_pair_does_not_matter(self) -> None:
        pairs = [(A, B), (B, C), (D, E)]
        expected = {A: A, B: A, C: A, D: D, E: D}
        for order in itertools.permutations(pairs):
            for flips in itertools.product((False, True), repeat=len(pairs)):
                given = [
                    (b, a) if flip else (a, b) for (a, b), flip in zip(order, flips, strict=True)
                ]
                with self.subTest(pairs=given):
                    self.assertEqual(group_of(pairs_table(*given)), expected)

    def test_the_same_id_under_two_sources_is_two_listings(self) -> None:
        other = ("other", "syn-a")
        self.assertEqual(group_of(pairs_table((other, B))), {other: other, B: other})
        self.assertEqual(group_of(pairs_table((A, B))), {A: A, B: A})


def listing_table(with_detail: list[tuple[str, str]], without_detail: list[tuple[str, str]]):
    observed = datetime(2026, 10, 1, 9, tzinfo=UTC)
    keys = [(key, observed) for key in with_detail] + [(key, None) for key in without_detail]
    return pa.table(
        {
            "source": pa.array([key[0] for key, _ in keys], pa.string()),
            "source_listing_id": pa.array([key[1] for key, _ in keys], pa.string()),
            "detail_observed_at": pa.array([at for _, at in keys], pa.timestamp("us", tz="UTC")),
        }
    )


class PropertyGroupRowsTest(unittest.TestCase):
    def setUp(self) -> None:
        # A-B a pair; C-D-E a chain (C and E do not pair); F and G alone; H has no detail record.
        listing = listing_table([G, E, F, D, C, B, A], [H])
        pairs = pairs_table((A, B), (C, D), (D, E))
        self.rows = property_group_rows(listing, pairs, snapshot_id=SNAPSHOT_ID, built_at=BUILT_AT)

    def test_one_row_per_listing_with_a_detail_record_in_the_contract(self) -> None:
        self.assertEqual(self.rows.schema, CONTRACT)
        self.assertEqual(
            [(row["source"], row["source_listing_id"]) for row in self.rows.to_pylist()],
            [A, B, C, D, E, F, G],
        )

    def test_group_id_size_and_completeness(self) -> None:
        self.assertEqual(
            [
                (row["property_group_id"], row["group_size"], row["group_is_complete"])
                for row in self.rows.to_pylist()
            ],
            [
                ("syn:syn-a", 2, True),
                ("syn:syn-a", 2, True),
                ("syn:syn-c", 3, False),
                ("syn:syn-c", 3, False),
                ("syn:syn-c", 3, False),
                ("syn:syn-f", 1, True),
                ("syn:syn-g", 1, True),
            ],
        )

    def test_every_row_carries_the_rule_version_the_snapshot_and_the_build_time(self) -> None:
        for row in self.rows.to_pylist():
            with self.subTest(listing=row["source_listing_id"]):
                self.assertEqual(row["property_group_version"], "pg1")
                self.assertEqual(row["silver_listing_snapshot_id"], SNAPSHOT_ID)
                self.assertEqual(row["built_at"], BUILT_AT)
                self.assertEqual(row["built_at"].utcoffset().total_seconds(), 0)

    def test_a_complete_group_of_three(self) -> None:
        rows = property_group_rows(
            listing_table([A, B, C], []),
            pairs_table((A, B), (A, C), (B, C)),
            snapshot_id=SNAPSHOT_ID,
            built_at=BUILT_AT,
        )
        self.assertEqual(rows.column("group_is_complete").to_pylist(), [True, True, True])
        self.assertEqual(rows.column("group_size").to_pylist(), [3, 3, 3])


if __name__ == "__main__":
    unittest.main()
