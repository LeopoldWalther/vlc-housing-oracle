"""Silver walk carries every run report as landing states it."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from src.contracts import landing_v1_columns
from src.silver.landing import register_landing
from src.silver.rebuild import build_tables
from src.synthetic.landing import write_landing_tables

RUN_START = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
RUN_END = datetime(2026, 10, 1, 6, 40, tzinfo=UTC)

REPORTS = [
    {
        "source": "syn",
        "area": "villa-norte-rent",
        "run_started_at": RUN_START,
        "run_ended_at": RUN_END,
        "observed_date": date(2026, 10, 1),
        "complete": True,
        "is_replay": False,
        "is_replay_source": "recorded",
        "pages": 4,
        "result_count": 112,
        "duration_seconds": 900.5,
    },
    {
        "source": "syn",
        "area": "villa-sur-sale",
        "run_started_at": RUN_START,
        "run_ended_at": RUN_END,
        "observed_date": date(2026, 10, 1),
        "complete": True,
        "is_replay": True,
        "is_replay_source": "recorded",
        "pages": 2,
        "result_count": 40,
        "duration_seconds": 1499.5,
    },
    {
        "source": "syn",
        "area": "villa-norte-rent",
        "observed_date": date(2026, 9, 1),
        "pages": 3,
        "result_count": 90,
        "duration_seconds": 600.0,
    },
]


class TestSilverWalk(unittest.TestCase):
    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write_landing_tables(directory, {"walk": REPORTS})
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        register_landing(connection, directory)
        self.walk = build_tables(connection)["walk"]

    def test_every_report_is_one_row_with_its_contract_columns_unchanged(self) -> None:
        contract_columns = [name for name, _ in landing_v1_columns()["walk"]]
        rows = self.walk.select(contract_columns).to_pylist()

        expected = [{name: report.get(name) for name in contract_columns} for report in REPORTS]
        sort_key = lambda row: (row["area"], str(row["run_started_at"]))  # noqa: E731
        self.assertEqual(sorted(expected, key=sort_key), sorted(rows, key=sort_key))

    def test_every_row_carries_the_transformation_version(self) -> None:
        self.assertEqual({"silver-v1"}, set(self.walk.column("transformation_version").to_pylist()))

    def test_column_types(self) -> None:
        schema = self.walk.schema

        self.assertEqual(pa.timestamp("us", tz="UTC"), schema.field("run_started_at").type)
        self.assertEqual(pa.date32(), schema.field("observed_date").type)
        self.assertEqual(pa.int32(), schema.field("pages").type)
        self.assertEqual(pa.bool_(), schema.field("complete").type)


if __name__ == "__main__":
    unittest.main()
