"""The ingest log is Silver's checkpoint: one row per processed landing run, written last."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.types import StringType

from src.contracts import landing_v3_sha256
from src.silver.commits import parse_commit
from src.silver.ingest import ingested_runs, record_ingested
from src.silver.sink import SilverSchemaError

NAMESPACE = "silver_test"
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
VERSION = "silver-v1"
TABLES = ("walk", "observation", "listing_detail", "listing_text")


def commit(run_id: str, started: str, observation_days: list[str]):
    days = {table: ["2026-10-20"] for table in TABLES} | {"observation": observation_days}
    return parse_commit(
        {
            "contract": "landing",
            "version": "v3",
            "schema_sha256": landing_v3_sha256(),
            "run_id": run_id,
            "run_started_at": started,
            "outcome": "ok",
            "is_replay": False,
            "row_counts": dict.fromkeys(TABLES, 1),
            "observed_dates": days,
            "written_at": "2026-10-20T05:50:12Z",
        }
    )


class TestIngestLog(unittest.TestCase):
    def setUp(self) -> None:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.catalog = SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )
        self.first = commit("20261020T030000Z", "2026-10-20T03:00:00Z", ["2026-10-20"])
        self.second = commit(
            "20261020T060000Z", "2026-10-20T06:00:00Z", ["2026-10-20", "2026-10-21"]
        )

    def test_an_absent_log_reads_as_empty(self) -> None:
        self.assertEqual({}, ingested_runs(self.catalog, NAMESPACE))

    def test_recorded_runs_come_back_with_their_days_and_start(self) -> None:
        record_ingested(self.catalog, NAMESPACE, [self.first, self.second], NOW, VERSION)

        runs = ingested_runs(self.catalog, NAMESPACE)

        self.assertEqual({"20261020T030000Z", "20261020T060000Z"}, set(runs))
        second = runs["20261020T060000Z"]
        self.assertEqual(
            {date(2026, 10, 20), date(2026, 10, 21)}, second.observed_dates["observation"]
        )
        self.assertEqual(datetime(2026, 10, 20, 6, 0, tzinfo=UTC), second.run_started_at)
        versions = (
            self.catalog.load_table(f"{NAMESPACE}.landing_ingest")
            .scan()
            .to_arrow()
            .column("transformation_version")
            .to_pylist()
        )
        self.assertEqual({VERSION}, set(versions))

    def test_recording_the_same_runs_twice_leaves_one_row_per_run(self) -> None:
        record_ingested(self.catalog, NAMESPACE, [self.first, self.second], NOW, VERSION)
        record_ingested(self.catalog, NAMESPACE, [self.second], NOW, VERSION)

        table = self.catalog.load_table(f"{NAMESPACE}.landing_ingest")
        run_ids = table.scan().to_arrow().column("run_id").to_pylist()
        self.assertEqual(sorted(["20261020T030000Z", "20261020T060000Z"]), sorted(run_ids))

    def test_a_refused_write_leaves_the_log_unchanged(self) -> None:
        record_ingested(self.catalog, NAMESPACE, [self.first], NOW, VERSION)
        table = self.catalog.load_table(f"{NAMESPACE}.landing_ingest")
        with table.update_schema() as schema:
            schema.add_column("unexpected", StringType())

        with self.assertRaises(SilverSchemaError):
            record_ingested(self.catalog, NAMESPACE, [self.second], NOW, VERSION)

        self.assertEqual({"20261020T030000Z"}, set(ingested_runs(self.catalog, NAMESPACE)))


if __name__ == "__main__":
    unittest.main()
