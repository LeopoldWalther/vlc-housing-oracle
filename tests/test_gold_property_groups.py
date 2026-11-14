"""Property groups: connected components of the pairs, and the dataset's rows."""

import itertools
import unittest

import pyarrow as pa

from src.gold.property_group import group_of

A, B, C, D, E = (("syn", f"syn-{name}") for name in "abcde")


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


if __name__ == "__main__":
    unittest.main()
