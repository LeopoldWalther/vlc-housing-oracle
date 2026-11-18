"""The synthetic census sections are a zipped shapefile shaped like the statistics office's."""

import tempfile
import unittest
import zipfile
from pathlib import Path, PurePosixPath

import duckdb

from src.synthetic.reference import OTHER_PROVINCE, PROVINCE, SYNTHETIC_CRS, write_census_archive

FIELDS = ("CUSEC", "CUDIS", "CUMUN", "CPRO", "NMUN")


class TestSyntheticCensusArchive(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.archive = Path(cls.directory.name) / "sections.zip"
        write_census_archive(cls.archive)
        with zipfile.ZipFile(cls.archive) as archive:
            cls.names = [PurePosixPath(name) for name in archive.namelist()]
        shapefile = next(name for name in cls.names if name.suffix == ".shp")
        cls.connection = duckdb.connect()
        cls.addClassCleanup(cls.connection.close)
        cls.connection.execute("LOAD spatial")
        cls.connection.execute(
            f"CREATE TABLE sections AS SELECT * FROM ST_Read('/vsizip/{cls.archive}/{shapefile}')"
        )

    def sections_holding(self, *, latitude: float, longitude: float) -> list[str]:
        """Section codes whose polygon holds the point, reprojected with longitude first."""
        rows = self.connection.execute(
            f"""
            SELECT CUSEC FROM sections
            WHERE ST_Intersects(
                ST_Transform(geom, '{SYNTHETIC_CRS}', 'EPSG:4326', always_xy := true),
                ST_Point(?, ?)
            )
            ORDER BY CUSEC
            """,
            [longitude, latitude],
        ).fetchall()
        return [code for (code,) in rows]

    def test_the_archive_holds_one_shapefile_in_a_sub_folder_with_its_companions(self) -> None:
        shapefiles = [name for name in self.names if name.suffix == ".shp"]
        self.assertEqual(len(shapefiles), 1)
        shapefile = shapefiles[0]
        self.assertEqual(len(shapefile.parts), 2)
        for suffix in (".dbf", ".shx", ".prj"):
            self.assertIn(shapefile.with_suffix(suffix), self.names)

    def test_it_has_four_polygons_with_the_five_fields(self) -> None:
        described = self.connection.execute("DESCRIBE sections").fetchall()
        columns = {name: column_type for name, column_type, *_ in described}
        for field in FIELDS:
            self.assertEqual(columns[field], "VARCHAR")
        rows = self.connection.execute(
            "SELECT CUSEC, CUDIS, CUMUN, CPRO, NMUN, ST_GeometryType(geom) FROM sections"
            " ORDER BY CUSEC"
        ).fetchall()
        self.assertEqual(
            [row[:5] for row in rows],
            [
                ("9800101001", "9800101", "98001", "98", "Otra"),
                ("9900101001", "9900101", "99001", "99", "Alba"),
                ("9900101002", "9900101", "99001", "99", "Alba"),
                ("9900201001", "9900201", "99002", "99", "Brisa"),
            ],
        )
        self.assertEqual({row[5] for row in rows}, {"POLYGON"})

    def test_the_provinces_are_codes_no_real_province_has(self) -> None:
        self.assertEqual((PROVINCE, OTHER_PROVINCE), ("99", "98"))

    def test_the_rectangles_are_wider_than_high_in_the_projected_system(self) -> None:
        sizes = self.connection.execute(
            """
            SELECT CUSEC, round(ST_XMax(geom) - ST_XMin(geom)), round(ST_YMax(geom) - ST_YMin(geom))
            FROM sections ORDER BY CUSEC
            """
        ).fetchall()
        self.assertEqual(
            sizes,
            [
                ("9800101001", 1600, 1000),
                ("9900101001", 1600, 1000),
                ("9900101002", 800, 500),
                ("9900201001", 1600, 1000),
            ],
        )

    def test_a_point_near_the_alba_centre_lies_in_both_alba_rectangles(self) -> None:
        self.assertEqual(
            self.sections_holding(latitude=39.4709, longitude=-0.37),
            ["9900101001", "9900101002"],
        )

    def test_a_point_far_north_of_alba_lies_in_no_rectangle(self) -> None:
        self.assertEqual(self.sections_holding(latitude=39.605, longitude=-0.37), [])

    def test_the_alba_point_with_swapped_axes_lies_in_no_rectangle(self) -> None:
        self.assertEqual(self.sections_holding(latitude=-0.37, longitude=39.4709), [])

    def test_a_point_600_m_east_of_the_centre_lies_in_the_wide_rectangle_only(self) -> None:
        # 0.007 degrees of longitude are about 600 m here: inside the 1,600 m width, outside a
        # rectangle whose axes were swapped (500 m to its eastern edge).
        self.assertEqual(self.sections_holding(latitude=39.47, longitude=-0.363), ["9900101001"])

    def test_writing_to_an_existing_path_is_refused(self) -> None:
        before = self.archive.read_bytes()
        with self.assertRaises(FileExistsError):
            write_census_archive(self.archive)
        self.assertEqual(self.archive.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
