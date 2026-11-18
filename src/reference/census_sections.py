"""Convert the statistics office's census-section archive into one immutable reference vintage.

An operator downloads the yearly archive, a zip that holds one shapefile of the whole country in a
projected coordinate system. This command keeps one province, reprojects it to WGS84 and writes

    <out>/census_sections/vintage=<vintage>/part-0.parquet
    <out>/census_sections/vintage=<vintage>/_manifest.json

The manifest names the source, the licence and the SHA-256 of the archive, so a vintage can be
traced to the file it came from (ADR 0004). A vintage is written once: an existing directory is
refused. Nothing here touches the network.

Usage:
    python -m src.reference.census_sections --archive <zip> --province <2 digits>
        --crs <EPSG code> --vintage <date> --source <url> --licence <text>
        --retrieved <date> --out <dir>
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

DATASET = "census_sections"

# The shapefile's fields and the column each becomes. CPRO, the province, is the filter.
SECTION_FIELDS = {
    "CUSEC": "section_code",
    "CUDIS": "district_code",
    "CUMUN": "municipality_code",
    "NMUN": "municipality_name",
}
PROVINCE_FIELD = "CPRO"

# Longitude is x: without always_xy the authority's axis order (latitude first) would apply.
_SECTIONS = f"""
SELECT
    {", ".join(f"{field} AS {column}" for field, column in SECTION_FIELDS.items())},
    ST_AsWKB(ST_Transform(geom, $crs, 'EPSG:4326', always_xy := true))::BLOB AS geometry,
    $vintage AS vintage
FROM ST_Read($shapefile)
WHERE {PROVINCE_FIELD} = $province
ORDER BY section_code
"""


class ReferenceArchiveError(ValueError):
    """The archive cannot become a vintage; the message names the cause."""


def _shapefile_in(archive: Path) -> str:
    """The path GDAL reads the archive's one shapefile under, without unpacking it."""
    with zipfile.ZipFile(archive) as opened:
        shapefiles = [name for name in opened.namelist() if name.lower().endswith(".shp")]
    if len(shapefiles) != 1:
        raise ReferenceArchiveError(
            f"{archive} holds {len(shapefiles)} shapefiles; exactly one is expected"
        )
    return f"/vsizip/{archive}/{shapefiles[0]}"


def _check_fields(connection: duckdb.DuckDBPyConnection, shapefile: str, archive: Path) -> None:
    described = connection.execute("DESCRIBE SELECT * FROM ST_Read(?)", [shapefile]).fetchall()
    present = {name for name, *_ in described}
    missing = [field for field in (*SECTION_FIELDS, PROVINCE_FIELD) if field not in present]
    if missing:
        raise ReferenceArchiveError(f"the shapefile in {archive} lacks {', '.join(missing)}")


def convert(
    archive: Path,
    *,
    province: str,
    crs: str,
    vintage: str,
    source: str,
    licence: str,
    retrieved: str,
    out: Path,
) -> Path:
    """Write the vintage directory for one province of ``archive`` and return its path.

    ``crs`` is the archive's coordinate system; the result is WGS84 with longitude first.
    Raises ``FileExistsError`` for an existing vintage and ``ReferenceArchiveError`` for an
    archive that cannot be converted; in both cases nothing is written.
    """
    vintage_directory = out / DATASET / f"vintage={vintage}"
    if vintage_directory.exists():
        raise FileExistsError(f"{vintage_directory} exists; a vintage is written once")
    shapefile = _shapefile_in(archive)
    connection = duckdb.connect()
    try:
        connection.execute("LOAD spatial")
        _check_fields(connection, shapefile, archive)
        sections = connection.execute(
            _SECTIONS,
            {"crs": crs, "vintage": vintage, "shapefile": shapefile, "province": province},
        ).to_arrow_table()
    finally:
        connection.close()
    if sections.num_rows == 0:
        raise ReferenceArchiveError(f"{archive} holds no section of province {province}")
    occurrences = sections.column("section_code").value_counts().field("counts").to_pylist()
    repeated = sum(count > 1 for count in occurrences)
    if repeated:
        plural = "code occurs" if repeated == 1 else "codes occur"
        raise ReferenceArchiveError(
            f"in {archive}, {repeated} section {plural} more than once in province {province}"
        )
    with archive.open("rb") as opened:
        archive_sha256 = hashlib.file_digest(opened, "sha256").hexdigest()
    manifest = {
        "dataset": DATASET,
        "vintage": vintage,
        "source": source,
        "licence": licence,
        "retrieved": retrieved,
        "archive_sha256": archive_sha256,
        "province": province,
        "crs": crs,
        "rows": sections.num_rows,
    }
    vintage_directory.mkdir(parents=True)
    pq.write_table(sections, vintage_directory / "part-0.parquet")
    (vintage_directory / "_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return vintage_directory


def _iso_date(text: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date like 2026-01-01") from error


def _province_code(text: str) -> str:
    if not re.fullmatch(r"\d{2}", text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a two-digit province code")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    """Convert one archive; exit 2 with the cause when it is refused."""
    parser = argparse.ArgumentParser(
        description="Convert a census-section archive into an immutable reference vintage."
    )
    parser.add_argument("--archive", required=True, type=Path, help="the downloaded zip archive")
    parser.add_argument("--province", required=True, type=_province_code, help="two digits")
    parser.add_argument("--crs", required=True, help="the archive's system, like EPSG:25830")
    parser.add_argument("--vintage", required=True, type=_iso_date, help="the edition's date")
    parser.add_argument("--source", required=True, help="the address the archive came from")
    parser.add_argument("--licence", required=True, help="the licence and its attribution line")
    parser.add_argument("--retrieved", required=True, type=_iso_date, help="the download's date")
    parser.add_argument("--out", required=True, type=Path, help="the reference root to write to")
    arguments = parser.parse_args(argv)
    try:
        vintage_directory = convert(
            arguments.archive,
            province=arguments.province,
            crs=arguments.crs,
            vintage=arguments.vintage,
            source=arguments.source,
            licence=arguments.licence,
            retrieved=arguments.retrieved,
            out=arguments.out,
        )
    except (ReferenceArchiveError, FileExistsError) as error:
        parser.error(str(error))
    rows = pq.read_metadata(vintage_directory / "part-0.parquet").num_rows
    print(f"wrote {rows} sections to {vintage_directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
