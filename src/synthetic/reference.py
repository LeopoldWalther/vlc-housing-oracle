"""Write synthetic census sections as a zipped shapefile, shaped like the statistics office's.

Every code and name here is invented: the province codes 99 and 98 belong to no real province, and
the rectangles surround the coordinates of the synthetic landing, not any real boundary. Tests of
the reference converter and of the location rule run on this archive, so the real boundary file
never enters the repository.
"""

import tempfile
import zipfile
from pathlib import Path

import duckdb

# The projected system of the real file: ETRS89 / UTM zone 30N, in metres.
SYNTHETIC_CRS = "EPSG:25830"
PROVINCE = "99"
OTHER_PROVINCE = "98"

# The archive's one folder; the real one has a non-ASCII name too.
_FOLDER = "Sección_sintética"
_STEM = "SECC_SYN"

# section code, municipality name, centre latitude, centre longitude, width and height in metres.
# Wider than high, so a reader that swaps the axes finds its points outside.
_SECTIONS = (
    ("9900101001", "Alba", 39.47, -0.37, 1_600, 1_000),
    ("9900101002", "Alba", 39.47, -0.37, 800, 500),
    ("9900201001", "Brisa", 39.60, -0.50, 1_600, 1_000),
    ("9800101001", "Otra", 40.60, -1.50, 1_600, 1_000),
)

_SHAPEFILE = """
COPY (
    WITH section AS (
        SELECT
            *,
            ST_Transform(
                ST_Point(longitude, latitude), 'EPSG:4326', '{crs}', always_xy := true
            ) AS centre
        FROM (VALUES {values}) AS t (section_code, name, latitude, longitude, width, height)
    )
    SELECT
        section_code AS CUSEC,
        section_code[:7] AS CUDIS,
        section_code[:5] AS CUMUN,
        section_code[:2] AS CPRO,
        name AS NMUN,
        ST_MakeEnvelope(
            ST_X(centre) - width / 2, ST_Y(centre) - height / 2,
            ST_X(centre) + width / 2, ST_Y(centre) + height / 2
        ) AS geom
    FROM section
) TO '{target}' WITH (FORMAT GDAL, DRIVER 'ESRI Shapefile', SRS '{crs}')
"""


def write_census_archive(path: Path) -> None:
    """Write the archive to ``path``: one shapefile in a sub-folder; an existing path is refused."""
    if path.exists():
        raise FileExistsError(f"{path} exists; a synthetic archive is written once")
    values = ", ".join(str(section) for section in _SECTIONS)
    with tempfile.TemporaryDirectory() as scratch:
        connection = duckdb.connect()
        try:
            connection.execute("LOAD spatial")
            connection.execute(
                _SHAPEFILE.format(
                    crs=SYNTHETIC_CRS, values=values, target=Path(scratch) / f"{_STEM}.shp"
                )
            )
        finally:
            connection.close()
        with zipfile.ZipFile(path, "x") as archive:
            for part in sorted(Path(scratch).iterdir()):
                archive.write(part, f"{_FOLDER}/{part.name}")
