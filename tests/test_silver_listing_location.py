"""listing_location: the census section, district and municipality a listing's coordinates lie in."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from src.reference.census_sections import convert
from src.silver.rebuild import (
    LOCATION_LISTING_COLUMNS,
    LOCATION_TABLE,
    listing_locations,
)
from src.synthetic.reference import PROVINCE, SYNTHETIC_CRS, write_census_archive

OBSERVED = datetime(2026, 10, 5, 8, tzinfo=UTC)
VINTAGE = "2026-01-01"

LISTING_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("detail_observed_at", pa.timestamp("us", tz="UTC")),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
    ]
)

LOCATION_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("municipality_code", pa.string()),
        ("district_code", pa.string()),
        ("section_code", pa.string()),
        ("reference_vintage", pa.string()),
        ("transformation_version", pa.string()),
    ]
)

WIDE_ALBA = ("99001", "9900101", "9900101001")
BRISA = ("99002", "9900201", "9900201001")
NOWHERE = (None, None, None)


def listing(
    listing_id: str,
    latitude: float | None,
    longitude: float | None,
    *,
    source: str = "syn",
    detail_observed_at: datetime | None = OBSERVED,
) -> dict[str, object]:
    return {
        "source": source,
        "source_listing_id": listing_id,
        "detail_observed_at": detail_observed_at,
        "latitude": latitude,
        "longitude": longitude,
    }


class TestListingLocation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        write_census_archive(root / "sections.zip")
        vintage_directory = convert(
            root / "sections.zip",
            province=PROVINCE,
            crs=SYNTHETIC_CRS,
            vintage=VINTAGE,
            source="https://example.org/sections.zip",
            licence="invented",
            retrieved="2026-03-20",
            out=root / "reference",
        )
        cls.sections = pq.read_table(vintage_directory / "part-0.parquet")

    def setUp(self) -> None:
        self.connection = duckdb.connect()
        self.addCleanup(self.connection.close)

    def located(self, *listings: dict[str, object]) -> pa.Table:
        return listing_locations(
            self.connection, pa.Table.from_pylist(list(listings), LISTING_SCHEMA), self.sections
        )

    def codes(self, *listings: dict[str, object]) -> dict[str, tuple[str | None, ...]]:
        return {
            row["source_listing_id"]: (
                row["municipality_code"],
                row["district_code"],
                row["section_code"],
            )
            for row in self.located(*listings).to_pylist()
        }

    def a_corner_of_the_brisa_rectangle(self) -> tuple[float, float]:
        """Latitude and longitude of a vertex, exactly as the reference file stores it."""
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("LOAD spatial")
        connection.register("sections", self.sections)
        longitude, latitude = connection.execute(
            """
            SELECT ST_X(corner), ST_Y(corner)
            FROM (
                SELECT ST_PointN(ST_ExteriorRing(ST_GeomFromWKB(geometry)), 1) AS corner
                FROM sections WHERE section_code = '9900201001'
            )
            """
        ).fetchone()
        return latitude, longitude

    def test_the_table_and_the_columns_it_reads_are_named(self) -> None:
        self.assertEqual(LOCATION_TABLE, "listing_location")
        self.assertEqual(LOCATION_LISTING_COLUMNS, tuple(LISTING_SCHEMA.names))

    def test_a_listing_inside_one_rectangle_gets_its_three_codes(self) -> None:
        self.assertEqual(self.codes(listing("in-brisa", 39.60, -0.50)), {"in-brisa": BRISA})

    def test_a_listing_inside_two_rectangles_gets_the_codes_of_the_smaller_section_code(
        self,
    ) -> None:
        self.assertEqual(self.codes(listing("in-both", 39.4709, -0.37)), {"in-both": WIDE_ALBA})

    def test_longitude_is_the_east_west_axis(self) -> None:
        # 600 m east of the Alba centre: inside the 1,600 m width, outside a swapped rectangle.
        self.assertEqual(self.codes(listing("east", 39.47, -0.363)), {"east": WIDE_ALBA})
        self.assertEqual(self.codes(listing("swapped", -0.37, 39.47)), {"swapped": NOWHERE})

    def test_a_listing_on_a_rectangles_border_gets_that_rectangles_codes(self) -> None:
        latitude, longitude = self.a_corner_of_the_brisa_rectangle()
        self.assertEqual(self.codes(listing("corner", latitude, longitude)), {"corner": BRISA})

    def test_a_listing_without_coordinates_or_outside_every_rectangle_has_null_codes(
        self,
    ) -> None:
        located = self.located(
            listing("no-coordinates", None, None),
            listing("no-longitude", 39.47, None),
            listing("no-latitude", None, -0.37),
            listing("far-north", 39.605, -0.37),
            listing("in-brisa", 39.60, -0.50),
        )
        rows = {row["source_listing_id"]: row for row in located.to_pylist()}
        for listing_id in ("no-coordinates", "no-longitude", "no-latitude", "far-north"):
            row = rows[listing_id]
            self.assertEqual(
                (row["municipality_code"], row["district_code"], row["section_code"]), NOWHERE
            )
        self.assertEqual(located.column("reference_vintage").unique().to_pylist(), [VINTAGE])

    def test_a_listing_without_a_detail_record_has_no_row_and_every_other_exactly_one(
        self,
    ) -> None:
        located = self.located(
            listing("card-only", 39.60, -0.50, detail_observed_at=None),
            listing("in-both", 39.4709, -0.37),
            listing("in-brisa", 39.60, -0.50),
            listing("far-north", 39.605, -0.37),
        )
        self.assertEqual(
            located.column("source_listing_id").to_pylist(), ["far-north", "in-both", "in-brisa"]
        )

    def test_rows_are_ordered_by_source_then_listing(self) -> None:
        located = self.located(
            listing("b", 39.60, -0.50, source="syn"),
            listing("a", 39.60, -0.50, source="syn"),
            listing("z", 39.60, -0.50, source="other"),
        )
        self.assertEqual(
            list(
                zip(
                    located.column("source").to_pylist(),
                    located.column("source_listing_id").to_pylist(),
                    strict=True,
                )
            ),
            [("other", "z"), ("syn", "a"), ("syn", "b")],
        )

    def test_the_seven_columns_are_typed_and_carry_the_transformation_version(self) -> None:
        located = self.located(listing("in-brisa", 39.60, -0.50), listing("far", 39.605, -0.37))
        self.assertEqual(located.schema.remove_metadata(), LOCATION_SCHEMA)
        self.assertEqual(
            located.column("transformation_version").unique().to_pylist(), ["silver-v1"]
        )

    def test_no_listing_gives_no_row_with_the_same_columns(self) -> None:
        located = self.located()
        self.assertEqual(located.num_rows, 0)
        self.assertEqual(located.schema.remove_metadata(), LOCATION_SCHEMA)

    def test_the_session_keeps_no_relation_and_its_silver_tables_are_untouched(self) -> None:
        self.connection.execute("CREATE TEMP TABLE silver_listing AS SELECT 'kept' AS marker")
        self.located(listing("in-brisa", 39.60, -0.50))
        self.assertEqual(
            self.connection.execute("SELECT * FROM silver_listing").fetchall(), [("kept",)]
        )
        relations = self.connection.execute(
            """
            SELECT table_name FROM duckdb_tables() WHERE table_name LIKE 'location%'
            UNION ALL
            SELECT view_name FROM duckdb_views() WHERE view_name LIKE 'location%'
            """
        ).fetchall()
        self.assertEqual(relations, [])


if __name__ == "__main__":
    unittest.main()
