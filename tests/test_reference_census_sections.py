"""A census-section archive becomes one immutable vintage: a Parquet file and its manifest."""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from src.reference.census_sections import ReferenceArchiveError, convert, main
from src.synthetic.reference import PROVINCE, SYNTHETIC_CRS, write_census_archive

VINTAGE = "2026-01-01"
DESCRIPTION = {
    "province": PROVINCE,
    "crs": SYNTHETIC_CRS,
    "vintage": VINTAGE,
    "source": "https://example.org/sections.zip",
    "licence": "CC BY 4.0; invented attribution",
    "retrieved": "2026-03-20",
}

FIVE_FIELDS = "'9900101001' AS CUSEC, '9900101' AS CUDIS, '99001' AS CUMUN, '99' AS CPRO"


def shapefile_archive(path: Path, select: str) -> None:
    """Zip a shapefile holding the rows of ``select``, which names its geometry ``geom``."""
    with tempfile.TemporaryDirectory() as scratch:
        connection = duckdb.connect()
        connection.execute("LOAD spatial")
        connection.execute(
            f"COPY ({select}) TO '{scratch}/variant.shp'"
            f" WITH (FORMAT GDAL, DRIVER 'ESRI Shapefile', SRS '{SYNTHETIC_CRS}')"
        )
        connection.close()
        with zipfile.ZipFile(path, "x") as archive:
            for part in sorted(Path(scratch).iterdir()):
                archive.write(part, f"folder/{part.name}")


def files_under(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


class TestConversion(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = Path(cls.directory.name)
        cls.archive = root / "sections.zip"
        write_census_archive(cls.archive)
        cls.out = root / "reference"
        cls.vintage_directory = convert(cls.archive, out=cls.out, **DESCRIPTION)
        cls.table = pq.read_table(cls.vintage_directory / "part-0.parquet")
        cls.manifest = json.loads((cls.vintage_directory / "_manifest.json").read_text())

    def sections_holding(self, *, latitude: float, longitude: float) -> list[str]:
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        connection.execute("LOAD spatial")
        rows = connection.execute(
            """
            SELECT section_code FROM read_parquet(?)
            WHERE ST_Intersects(ST_GeomFromWKB(geometry), ST_Point(?, ?))
            ORDER BY section_code
            """,
            [str(self.vintage_directory / "part-0.parquet"), longitude, latitude],
        ).fetchall()
        return [code for (code,) in rows]

    def test_the_vintage_directory_lies_under_the_dataset(self) -> None:
        self.assertEqual(
            self.vintage_directory, self.out / "census_sections" / f"vintage={VINTAGE}"
        )
        self.assertEqual(
            sorted(path.name for path in self.vintage_directory.iterdir()),
            ["_manifest.json", "part-0.parquet"],
        )

    def test_the_file_has_the_six_columns_in_order(self) -> None:
        self.assertEqual(
            [(field.name, field.type) for field in self.table.schema],
            [
                ("section_code", pa.string()),
                ("district_code", pa.string()),
                ("municipality_code", pa.string()),
                ("municipality_name", pa.string()),
                ("geometry", pa.binary()),
                ("vintage", pa.string()),
            ],
        )

    def test_it_keeps_the_province_ordered_by_section_with_the_vintage_on_every_row(self) -> None:
        rows = self.table.drop_columns(["geometry"]).to_pylist()
        self.assertEqual(
            [tuple(row.values()) for row in rows],
            [
                ("9900101001", "9900101", "99001", "Alba", VINTAGE),
                ("9900101002", "9900101", "99001", "Alba", VINTAGE),
                ("9900201001", "9900201", "99002", "Brisa", VINTAGE),
            ],
        )

    def test_the_geometry_is_wgs84_with_longitude_first(self) -> None:
        self.assertEqual(
            self.sections_holding(latitude=39.4709, longitude=-0.37),
            ["9900101001", "9900101002"],
        )
        self.assertEqual(self.sections_holding(latitude=39.47, longitude=-0.363), ["9900101001"])
        self.assertEqual(self.sections_holding(latitude=-0.37, longitude=39.4709), [])

    def test_the_manifest_describes_the_vintage_and_hashes_the_archive(self) -> None:
        self.assertEqual(
            self.manifest,
            {
                "dataset": "census_sections",
                "vintage": VINTAGE,
                "source": DESCRIPTION["source"],
                "licence": DESCRIPTION["licence"],
                "retrieved": DESCRIPTION["retrieved"],
                "archive_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                "province": PROVINCE,
                "crs": SYNTHETIC_CRS,
                "rows": 3,
            },
        )

    def test_a_second_conversion_into_the_vintage_is_refused_and_changes_no_file(self) -> None:
        before = files_under(self.out)
        with self.assertRaises(FileExistsError) as refused:
            convert(self.archive, out=self.out, **DESCRIPTION)
        self.assertIn(str(self.vintage_directory), str(refused.exception))
        self.assertEqual(files_under(self.out), before)

    def test_another_vintage_lies_beside_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch)
            convert(self.archive, out=out, **DESCRIPTION)
            second = convert(self.archive, out=out, **DESCRIPTION | {"vintage": "2027-01-01"})
            self.assertEqual(
                pq.read_table(second / "part-0.parquet").column("vintage").unique().to_pylist(),
                ["2027-01-01"],
            )
            self.assertEqual(
                sorted(path.name for path in (out / "census_sections").iterdir()),
                ["vintage=2026-01-01", "vintage=2027-01-01"],
            )


class TestRefusals(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.out = self.root / "reference"

    def refused(self, archive: Path, **changed: str) -> str:
        with self.assertRaises(ReferenceArchiveError) as refused:
            convert(archive, out=self.out, **DESCRIPTION | changed)
        self.assertFalse(self.out.exists(), "a refused conversion writes nothing")
        return str(refused.exception)

    def test_an_archive_with_two_shapefiles_is_refused(self) -> None:
        archive = self.root / "two.zip"
        write_census_archive(archive)
        with zipfile.ZipFile(archive, "a") as opened:
            opened.writestr("other/second.shp", b"")
        message = self.refused(archive)
        self.assertIn("2 shapefiles", message)
        self.assertIn(str(archive), message)

    def test_an_archive_without_a_shapefile_is_refused(self) -> None:
        archive = self.root / "none.zip"
        with zipfile.ZipFile(archive, "x") as opened:
            opened.writestr("folder/readme.txt", "no shapefile here")
        self.assertIn("0 shapefiles", self.refused(archive))

    def test_a_shapefile_without_the_municipality_name_is_refused_naming_the_field(self) -> None:
        archive = self.root / "no-name.zip"
        shapefile_archive(archive, f"SELECT {FIVE_FIELDS}, ST_MakeEnvelope(0, 0, 100, 100) AS geom")
        self.assertIn("NMUN", self.refused(archive))

    def test_a_province_without_a_section_is_refused(self) -> None:
        archive = self.root / "sections.zip"
        write_census_archive(archive)
        self.assertIn("province 97", self.refused(archive, province="97"))

    def test_a_section_code_that_occurs_twice_is_refused_naming_the_count(self) -> None:
        archive = self.root / "twice.zip"
        shapefile_archive(
            archive,
            f"SELECT {FIVE_FIELDS}, 'Alba' AS NMUN, ST_MakeEnvelope(0, 0, 100, 100) AS geom"
            " FROM range(2)",
        )
        self.assertIn("1 section code occurs more than once", self.refused(archive))


class TestCommand(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = self.root / "sections.zip"
        write_census_archive(self.archive)
        self.out = self.root / "reference"

    def arguments(self, **changed: str) -> list[str]:
        values = DESCRIPTION | {"archive": str(self.archive), "out": str(self.out)} | changed
        return [part for name, value in values.items() for part in (f"--{name}", value)]

    def exit_of(self, arguments: list[str]) -> tuple[int, str]:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as stopped:
            main(arguments)
        return stopped.exception.code, errors.getvalue()

    def test_it_writes_the_vintage_and_names_it(self) -> None:
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = main(self.arguments())
        vintage_directory = self.out / "census_sections" / f"vintage={VINTAGE}"
        self.assertEqual(status, 0)
        self.assertEqual(pq.read_table(vintage_directory / "part-0.parquet").num_rows, 3)
        self.assertEqual(printed.getvalue(), f"wrote 3 sections to {vintage_directory}\n")

    def test_every_argument_is_required(self) -> None:
        for name in ("archive", "province", "crs", "vintage", "source", "licence", "retrieved"):
            arguments = self.arguments()
            position = arguments.index(f"--{name}")
            code, message = self.exit_of(arguments[:position] + arguments[position + 2 :])
            self.assertEqual(code, 2)
            self.assertIn(f"--{name}", message)

    def test_a_refused_archive_exits_2_with_the_cause(self) -> None:
        code, message = self.exit_of(self.arguments(province="97"))
        self.assertEqual(code, 2)
        self.assertIn("province 97", message)

    def test_an_existing_vintage_exits_2(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            main(self.arguments())
        code, message = self.exit_of(self.arguments())
        self.assertEqual(code, 2)
        self.assertIn(f"vintage={VINTAGE}", message)

    def test_a_vintage_that_is_no_date_and_a_province_that_is_no_code_exit_2(self) -> None:
        for changed in ({"vintage": "2026"}, {"retrieved": "yesterday"}, {"province": "9"}):
            code, _ = self.exit_of(self.arguments(**changed))
            self.assertEqual(code, 2)
            self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
