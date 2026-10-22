"""Across collection runs, Silver keeps the newest sighting of a record, as v1's merge did."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from src.contracts import landing_v3_columns
from src.silver.landing import register_landing_v3
from src.silver.rebuild import build_tables
from src.synthetic.landing import write_landing_v3

MORNING, NOON = "20261003T060000Z", "20261003T120000Z"
REPLAY = "20261005T150000Z"
IMPORT = "import-20261003"
STARTED = {
    MORNING: datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
    NOON: datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    REPLAY: datetime(2026, 10, 5, 15, 0, tzinfo=UTC),
    IMPORT: datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
}
TABLES = tuple(landing_v3_columns())


def card(
    run_id: str, observed_at: datetime, price_eur: int, ingested_at: datetime | None = None
) -> dict:
    return {
        "source": "syn",
        "run_id": run_id,
        "area": "alba-sale",
        "operation": "sale",
        "municipality": "alba",
        "source_listing_id": "syn-1",
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
        "ingested_at": ingested_at or observed_at,
        "price_eur": price_eur,
        "is_empty_card": False,
    }


def detail_pair(
    run_id: str, observed_at: datetime, ingested_at: datetime, price_eur: int, text: str
) -> dict:
    key = {
        "source": "syn",
        "run_id": run_id,
        "area": "alba-sale",
        "source_listing_id": "syn-1",
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
        "ingested_at": ingested_at,
    }
    return {
        "listing_detail": [
            key | {"operation": "sale", "municipality": "alba", "price_eur": price_eur}
        ],
        "listing_text": [key | {"title": "Piso", "description": text}],
    }


def silver(rows_by_table: dict) -> dict[str, pa.Table]:
    with tempfile.TemporaryDirectory() as directory:
        runs = {run_id: {"run_started_at": started} for run_id, started in STARTED.items()}
        write_landing_v3(Path(directory), rows_by_table, runs)
        connection = duckdb.connect()
        register_landing_v3(connection, directory, dict.fromkeys(TABLES, set(STARTED)), STARTED, {})
        tables = build_tables(connection)
        connection.close()
    return tables


class TestNewestSighting(unittest.TestCase):
    def test_two_live_runs_of_one_day_keep_the_later_runs_card(self) -> None:
        rows = silver(
            {
                "observation": [
                    card(MORNING, datetime(2026, 10, 3, 7, 0, tzinfo=UTC), 250_000),
                    card(NOON, datetime(2026, 10, 3, 12, 5, tzinfo=UTC), 240_000),
                ]
            }
        )["observation"].to_pylist()

        self.assertEqual([(NOON, 240_000)], [(r["run_id"], r["price_eur"]) for r in rows])

    def test_a_replay_rederiving_a_detail_keeps_the_replays_detail_and_text(self) -> None:
        observed = datetime(2026, 10, 3, 7, 30, tzinfo=UTC)
        original = detail_pair(MORNING, observed, observed, 270_000, "Original.")
        replayed = detail_pair(
            REPLAY, observed, datetime(2026, 10, 5, 15, 30, tzinfo=UTC), 275_000, "Corrected."
        )
        tables = silver(
            {
                "observation": [card(MORNING, observed, 270_000)],
                "listing_detail": original["listing_detail"] + replayed["listing_detail"],
                "listing_text": original["listing_text"] + replayed["listing_text"],
            }
        )

        details = tables["listing_detail"].to_pylist()
        self.assertEqual([(REPLAY, 275_000)], [(r["run_id"], r["price_eur"]) for r in details])
        self.assertEqual(
            ["Corrected."], [r["description"] for r in tables["listing_text"].to_pylist()]
        )
        self.assertEqual(275_000, tables["listing"].to_pylist()[0]["price_eur"])

    def test_a_tie_on_instants_resolves_by_run_start_not_by_id(self) -> None:
        # "import-…" sorts after "2026…" as a string, but the morning run started later.
        observed = datetime(2026, 10, 3, 7, 0, tzinfo=UTC)
        rows = silver(
            {"observation": [card(IMPORT, observed, 200_000), card(MORNING, observed, 100_000)]}
        )["observation"].to_pylist()

        self.assertEqual([MORNING], [r["run_id"] for r in rows])

    def test_silver_listing_detail_carries_every_landing_column(self) -> None:
        observed = datetime(2026, 10, 3, 7, 30, tzinfo=UTC)
        table = silver(detail_pair(MORNING, observed, observed, 270_000, "Piso."))["listing_detail"]

        expected = [name for name, _ in landing_v3_columns()["listing_detail"]] + [
            "transformation_version"
        ]
        self.assertEqual(expected, table.schema.names)
        self.assertEqual({"silver-v1"}, set(table.column("transformation_version").to_pylist()))


if __name__ == "__main__":
    unittest.main()
