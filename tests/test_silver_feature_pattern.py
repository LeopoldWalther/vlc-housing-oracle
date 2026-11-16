"""feature_pattern: one row per feature wording, with its target and the listings that lack it."""

import unittest

import duckdb
import pyarrow as pa

from src.silver.rebuild import feature_patterns

TEXT_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("features", pa.list_(pa.string())),
    ]
)

# The few attribute columns the cases need; Silver's listing has many more.
LISTING_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("rooms", pa.int32()),
        ("has_pool_state", pa.string()),
        ("orientation", pa.list_(pa.string())),
    ]
)


def listing(listing_id: str, **attributes: object) -> dict:
    return {"source": "syn", "source_listing_id": listing_id} | attributes


def text(listing_id: str, *features: str) -> dict:
    return {"source": "syn", "source_listing_id": listing_id, "features": list(features)}


def patterns(listings: list[dict], texts: list[dict]) -> list[dict]:
    with duckdb.connect() as connection:
        return feature_patterns(
            connection,
            pa.Table.from_pylist(listings, schema=LISTING_SCHEMA),
            pa.Table.from_pylist(texts, schema=TEXT_SCHEMA),
        ).to_pylist()


def row(rows: list[dict], pattern: str) -> dict:
    (found,) = [row for row in rows if row["pattern"] == pattern]
    return found


class FeaturePatternTest(unittest.TestCase):
    def test_features_that_differ_only_in_a_number_are_one_wording(self) -> None:
        rows = patterns(
            [listing("syn-1", rooms=3), listing("syn-2", rooms=4)],
            [text("syn-1", "3 habitaciones"), text("syn-2", "4 habitaciones")],
        )

        self.assertEqual(
            rows,
            [
                {
                    "pattern": "# habitaciones",
                    "listings": 2,
                    "target": "rooms",
                    "listings_target_missing": 0,
                    "transformation_version": "silver-v1",
                }
            ],
        )

    def test_a_listing_with_one_wording_twice_counts_once(self) -> None:
        rows = patterns(
            [listing("syn-1", rooms=3)], [text("syn-1", "3 habitaciones", "2 habitaciones")]
        )

        self.assertEqual(row(rows, "# habitaciones")["listings"], 1)

    def test_an_invented_wording_has_no_target_and_no_missing_count(self) -> None:
        rows = patterns([listing("syn-1")], [text("syn-1", "Vistas al mar desde 2 miradores")])

        self.assertEqual(
            rows,
            [
                {
                    "pattern": "vistas al mar desde # miradores",
                    "listings": 1,
                    "target": None,
                    "listings_target_missing": None,
                    "transformation_version": "silver-v1",
                }
            ],
        )

    def test_a_null_an_unknown_and_an_empty_list_are_missing(self) -> None:
        rows = patterns(
            [
                listing("syn-1", rooms=None, has_pool_state="unknown", orientation=[]),
                listing("syn-2", rooms=2, has_pool_state="true", orientation=["south"]),
                listing("syn-3", rooms=2, has_pool_state="false_stated", orientation=None),
            ],
            [
                text(listing_id, "2 habitaciones", "Piscina", "Orientación sur")
                for listing_id in ("syn-1", "syn-2", "syn-3")
            ],
        )

        self.assertEqual(row(rows, "# habitaciones")["listings_target_missing"], 1)
        self.assertEqual(row(rows, "piscina")["listings_target_missing"], 1)
        self.assertEqual(row(rows, "orientación sur")["listings_target_missing"], 2)

    def test_a_label_or_unread_target_has_no_missing_count(self) -> None:
        rows = patterns(
            [listing("syn-1")],
            [text("syn-1", "Alquilada, con inquilinos", "Chimenea", "Amueblado y cocina equipada")],
        )

        self.assertEqual(
            {row["pattern"]: (row["target"], row["listings_target_missing"]) for row in rows},
            {
                "alquilada, con inquilinos": ("is_tenant_occupied", None),
                "chimenea": ("unread", None),
                "amueblado y cocina equipada": ("furnishing", None),
            },
        )

    def test_a_text_row_without_a_listing_row_misses_its_target(self) -> None:
        rows = patterns([listing("syn-1", rooms=3)], [text("syn-absent", "3 habitaciones")])

        self.assertEqual(row(rows, "# habitaciones")["listings_target_missing"], 1)

    def test_rows_are_ordered_by_listings_then_pattern(self) -> None:
        rows = patterns(
            [listing("syn-1"), listing("syn-2")],
            [
                text("syn-1", "Trastero", "Balcón", "Con ascensor"),
                text("syn-2", "Con ascensor", "sin features"),
                text("syn-3"),
            ],
        )

        self.assertEqual(
            [row["pattern"] for row in rows], ["con ascensor", "balcón", "sin features", "trastero"]
        )
        self.assertEqual(
            list(rows[0]),
            ["pattern", "listings", "target", "listings_target_missing", "transformation_version"],
        )

    def test_the_columns_have_the_documented_types(self) -> None:
        with duckdb.connect() as connection:
            table = feature_patterns(
                connection,
                pa.Table.from_pylist([listing("syn-1", rooms=3)], schema=LISTING_SCHEMA),
                pa.Table.from_pylist([text("syn-1", "3 habitaciones")], schema=TEXT_SCHEMA),
            )

        self.assertEqual(
            [(field.name, field.type) for field in table.schema],
            [
                ("pattern", pa.string()),
                ("listings", pa.int32()),
                ("target", pa.string()),
                ("listings_target_missing", pa.int32()),
                ("transformation_version", pa.string()),
            ],
        )


if __name__ == "__main__":
    unittest.main()
