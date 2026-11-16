"""listing_feature_label: what the portal's features state about a listing."""

import unittest

import duckdb
import pyarrow as pa

from src.silver.rebuild import listing_feature_labels

TEXT_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("features", pa.list_(pa.string())),
    ]
)

TENANT = "Alquilada, con inquilinos"
BARE_OWNERSHIP = "Nuda propiedad Qué es la nuda propiedad"


def text(listing_id: str, *features: str) -> dict:
    return {"source": "syn", "source_listing_id": listing_id, "features": list(features)}


def labels(*texts: dict) -> dict[str, dict]:
    with duckdb.connect() as connection:
        rows = listing_feature_labels(
            connection, pa.Table.from_pylist(list(texts), schema=TEXT_SCHEMA)
        ).to_pylist()
    return {row["source_listing_id"]: row for row in rows}


class SaleLabelTest(unittest.TestCase):
    def test_the_tenant_wording_makes_a_listing_tenant_occupied(self) -> None:
        row = labels(text("syn-1", "3 habitaciones", TENANT))["syn-1"]

        self.assertEqual((row["is_tenant_occupied"], row["is_bare_ownership"]), (True, False))

    def test_the_bare_ownership_wording_makes_a_listing_bare_ownership(self) -> None:
        row = labels(text("syn-1", BARE_OWNERSHIP))["syn-1"]

        self.assertEqual((row["is_tenant_occupied"], row["is_bare_ownership"]), (False, True))

    def test_a_listing_with_neither_wording_has_false_for_both(self) -> None:
        row = labels(text("syn-1", "3 habitaciones", "Con ascensor"))["syn-1"]

        self.assertEqual((row["is_tenant_occupied"], row["is_bare_ownership"]), (False, False))

    def test_a_listing_without_features_has_a_row_with_false_for_both(self) -> None:
        rows = labels(
            text("syn-1"), {"source": "syn", "source_listing_id": "syn-2", "features": None}
        )

        for listing_id in ("syn-1", "syn-2"):
            with self.subTest(listing=listing_id):
                row = rows[listing_id]
                self.assertEqual(
                    (row["is_tenant_occupied"], row["is_bare_ownership"]), (False, False)
                )

    def test_one_row_per_text_row_in_key_order(self) -> None:
        with duckdb.connect() as connection:
            rows = listing_feature_labels(
                connection,
                pa.Table.from_pylist(
                    [text("syn-2", TENANT, TENANT), text("syn-1"), text("syn-3", BARE_OWNERSHIP)],
                    schema=TEXT_SCHEMA,
                ),
            ).to_pylist()

        self.assertEqual([row["source_listing_id"] for row in rows], ["syn-1", "syn-2", "syn-3"])
        self.assertEqual({row["source"] for row in rows}, {"syn"})
        self.assertEqual({row["transformation_version"] for row in rows}, {"silver-v1"})


if __name__ == "__main__":
    unittest.main()
