"""Descriptions lose phone numbers and names before they enter Silver; everything else survives.

Every value here is synthetic. Numbers use repeated digits so no fixture resembles a real line.
"""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.silver.landing import register_landing_v3
from src.silver.rebuild import build_tables
from src.synthetic.landing import write_landing_v3

# One synthetic collection run: the FEATURE-001 rules are checked on landing v3, as Silver reads it.
RUN_ID = "20261001T060000Z"
RUN_STARTED = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
LANDING_TABLES = ("walk", "observation", "listing_detail", "listing_text")


def write_one_run(directory: Path, rows_by_table: dict) -> None:
    rows = {
        table: [{"run_id": RUN_ID} | dict(row) for row in table_rows]
        for table, table_rows in rows_by_table.items()
    }
    write_landing_v3(directory, rows, runs={RUN_ID: {"run_started_at": RUN_STARTED}})


def register_one_run(connection: duckdb.DuckDBPyConnection, directory: Path) -> None:
    register_landing_v3(
        connection,
        str(directory),
        dict.fromkeys(LANDING_TABLES, {RUN_ID}),
        {RUN_ID: RUN_STARTED},
        {},
    )


def run_file(directory: Path, table: str) -> Path:
    return directory / table / f"run_date={RUN_STARTED.date()}" / f"run={RUN_ID}" / "part-0.parquet"


REDACTED = {
    "600 000 000": "[phone]",
    "+34600000000.": "[phone].",
    "0034 600-000-000": "[phone]",
    "+34 700 000 000h": "[phone]h",
    "Sra. García": "[name]",
    "Llame al 600 000 000 o al 699 999 999, Sra. García": "Llame al [phone] o al [phone], [name]",
}
UNCHANGED = ["690.000 €", "Ref 123456789", "reformado 2023"]


def silver_listing_text(descriptions: list[str], title: str = "Piso") -> list[dict[str, object]]:
    keys = [
        {
            "source": "syn",
            "area": "alba-sale",
            "source_listing_id": f"syn-{index}",
            "observed_at": datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            "observed_date": datetime(2026, 10, 1).date(),
        }
        for index, _ in enumerate(descriptions)
    ]
    texts = [
        key | {"title": title, "description": description}
        for key, description in zip(keys, descriptions, strict=True)
    ]
    with tempfile.TemporaryDirectory() as directory:
        write_one_run(Path(directory), {"listing_detail": keys, "listing_text": texts})
        connection = duckdb.connect()
        register_one_run(connection, Path(directory))
        rows = build_tables(connection)["listing_text"].to_pylist()
        connection.close()
    return sorted(rows, key=lambda row: int(str(row["source_listing_id"]).removeprefix("syn-")))


class TestRedaction(unittest.TestCase):
    def test_phone_numbers_and_names_are_replaced(self) -> None:
        rows = silver_listing_text(list(REDACTED))

        self.assertEqual(list(REDACTED.values()), [row["description"] for row in rows])

    def test_prices_references_and_years_survive(self) -> None:
        rows = silver_listing_text(UNCHANGED)

        self.assertEqual(UNCHANGED, [row["description"] for row in rows])

    def test_titles_stay_verbatim(self) -> None:
        rows = silver_listing_text(["Sin datos"], title="Piso en calle Don Juan")

        self.assertEqual("Piso en calle Don Juan", rows[0]["title"])

    def test_every_row_records_the_redaction_version(self) -> None:
        rows = silver_listing_text(["600 000 000", "Piso"])

        self.assertEqual(["r1", "r1"], [row["redaction_version"] for row in rows])


if __name__ == "__main__":
    unittest.main()
