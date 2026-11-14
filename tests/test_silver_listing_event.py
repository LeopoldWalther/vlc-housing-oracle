"""listing_event: a listing disappears at the first complete live walk of its area that misses it."""

import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog

from src.silver.rebuild import NAMESPACE, listing_events, local_commits, process_landing
from src.synthetic.landing import write_landing_v3

AREA = "alba-sale"

WALK_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("area", pa.string()),
        ("observed_date", pa.date32()),
        ("run_id", pa.string()),
        ("run_started_at", pa.timestamp("us", tz="UTC")),
        ("run_ended_at", pa.timestamp("us", tz="UTC")),
        ("complete", pa.bool_()),
        ("is_replay", pa.bool_()),
    ]
)

OBSERVATION_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("observed_date", pa.date32()),
        ("area", pa.string()),
        ("is_empty_card", pa.bool_()),
        ("run_id", pa.string()),
    ]
)

EVENT_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("event_type", pa.string()),
        ("event_date", pa.date32()),
        ("area", pa.string()),
        ("run_id", pa.string()),
        ("last_seen_date", pa.date32()),
        ("transformation_version", pa.string()),
    ]
)


def day(n: int) -> date:
    return date(2026, 10, n)


def run_id(n: int, area: str = AREA) -> str:
    return f"2026100{n}T0600Z-{area}" if n < 10 else f"202610{n}T0600Z-{area}"


def walk(
    n: int,
    *,
    area: str = AREA,
    complete: bool | None = True,
    is_replay: bool | None = False,
    run: str | None = None,
    started_hour: int = 6,
    duration: timedelta = timedelta(hours=1),
) -> dict:
    started = datetime(2026, 10, n, started_hour, tzinfo=UTC)
    return {
        "source": "syn",
        "area": area,
        "observed_date": day(n),
        "run_id": run or run_id(n, area),
        "run_started_at": started,
        "run_ended_at": started + duration,
        "complete": complete,
        "is_replay": is_replay,
    }


def sighting(
    n: int, listing_id: str = "L1", *, area: str = AREA, is_empty_card: bool = False
) -> dict:
    return {
        "source": "syn",
        "source_listing_id": listing_id,
        "observed_date": day(n),
        "area": area,
        "is_empty_card": is_empty_card,
        "run_id": run_id(n, area),
    }


def events(walks: list[dict], sightings: list[dict]) -> list[dict]:
    return listing_events(
        duckdb.connect(),
        pa.Table.from_pylist(walks, WALK_SCHEMA),
        pa.Table.from_pylist(sightings, OBSERVATION_SCHEMA),
    ).to_pylist()


class Disappearance(unittest.TestCase):
    def test_a_complete_live_walk_that_misses_the_listing_is_a_disappearance(self) -> None:
        rows = events([walk(1), walk(3)], [sighting(1)])

        self.assertEqual(
            rows,
            [
                {
                    "source": "syn",
                    "source_listing_id": "L1",
                    "event_type": "disappeared",
                    "event_date": day(3),
                    "area": AREA,
                    "run_id": run_id(3),
                    "last_seen_date": day(1),
                    "transformation_version": "silver-v1",
                }
            ],
        )

    def test_no_walk_between_two_sightings_is_no_event(self) -> None:
        self.assertEqual(events([walk(1), walk(3)], [sighting(1), sighting(3)]), [])


class NotEvidence(unittest.TestCase):
    def test_a_walk_that_is_not_a_complete_live_walk_of_the_area_is_no_evidence(self) -> None:
        cases = {
            "incomplete": walk(3, complete=False),
            "replay": walk(3, is_replay=True),
            "null complete and is_replay": walk(3, complete=None, is_replay=None),
            "null is_replay": walk(3, is_replay=None),
            "another area": walk(3, area="other-area"),
            "run ends on the next UTC date": walk(3, started_hour=23, duration=timedelta(hours=2)),
        }
        for name, missing in cases.items():
            with self.subTest(name):
                self.assertEqual(events([walk(1), missing], [sighting(1)]), [])

    def test_a_walk_on_the_sightings_day_or_earlier_is_no_evidence(self) -> None:
        self.assertEqual(events([walk(1), walk(2)], [sighting(2)]), [])

    def test_a_listing_only_ever_seen_as_an_empty_card_has_no_event(self) -> None:
        self.assertEqual(events([walk(1), walk(3)], [sighting(1, is_empty_card=True)]), [])

    def test_a_listing_is_judged_by_the_area_of_its_latest_real_card(self) -> None:
        rows = events(
            [walk(4, area="beta-sale"), walk(5)],
            [sighting(1), sighting(2, area="beta-sale", is_empty_card=True)],
        )

        self.assertEqual(
            [(row["event_date"], row["area"], row["last_seen_date"]) for row in rows],
            [(day(5), AREA, day(2))],
        )

    def test_an_empty_card_sighting_closes_the_gap(self) -> None:
        rows = events([walk(1), walk(3), walk(5)], [sighting(1), sighting(3, is_empty_card=True)])

        self.assertEqual(
            [(row["event_date"], row["last_seen_date"]) for row in rows], [(day(5), day(3))]
        )


if __name__ == "__main__":
    unittest.main()


def summary(rows: list[dict]) -> list[tuple]:
    return sorted(
        (row["event_date"], row["event_type"], row["area"], row["run_id"], row["last_seen_date"])
        for row in rows
    )


class Reappearance(unittest.TestCase):
    def test_the_first_sighting_after_a_disappearance_is_a_reappearance(self) -> None:
        rows = events([walk(1), walk(3), walk(5)], [sighting(1), sighting(5, area="beta-sale")])

        self.assertEqual(
            summary(rows),
            [
                (day(3), "disappeared", AREA, run_id(3), day(1)),
                (day(5), "reappeared", "beta-sale", run_id(5, "beta-sale"), day(1)),
            ],
        )

    def test_two_gaps_give_four_events(self) -> None:
        rows = events([walk(n) for n in (1, 3, 5, 7, 9)], [sighting(1), sighting(5), sighting(9)])

        self.assertEqual(
            [(row[0], row[1]) for row in summary(rows)],
            [
                (day(3), "disappeared"),
                (day(5), "reappeared"),
                (day(7), "disappeared"),
                (day(9), "reappeared"),
            ],
        )

    def test_a_gap_without_an_evidence_day_has_neither_event(self) -> None:
        self.assertEqual(events([walk(1), walk(5)], [sighting(1), sighting(5)]), [])

    def test_after_a_move_only_the_new_home_areas_walks_are_evidence(self) -> None:
        sightings = [sighting(1), sighting(2, area="beta-sale")]

        with self.subTest("old area"):
            self.assertEqual(events([walk(3)], sightings), [])
        with self.subTest("new area"):
            self.assertEqual(
                summary(events([walk(3, area="beta-sale")], sightings)),
                [(day(3), "disappeared", "beta-sale", run_id(3, "beta-sale"), day(2))],
            )


class Determinism(unittest.TestCase):
    def test_only_the_first_evidence_day_of_a_gap_is_a_disappearance(self) -> None:
        rows = events([walk(n) for n in (1, 3, 4, 6)], [sighting(1)])

        self.assertEqual(summary(rows), [(day(3), "disappeared", AREA, run_id(3), day(1))])

    def test_the_run_that_started_first_names_the_evidence_day(self) -> None:
        # An imported run's id sorts after the timestamped ids although it started earlier.
        rows = events(
            [walk(1), walk(3, started_hour=8), walk(3, run="import-alba", started_hour=5)],
            [sighting(1)],
        )

        self.assertEqual([row["run_id"] for row in rows], ["import-alba"])

    def test_rows_are_ordered_and_unique_per_listing_type_and_day(self) -> None:
        rows = events(
            [walk(n) for n in (1, 3, 5, 7)],
            [sighting(5, "L2"), sighting(1, "L1"), sighting(5, "L1"), sighting(1, "L2")],
        )
        keys = [
            (row["source"], row["source_listing_id"], row["event_date"], row["event_type"])
            for row in rows
        ]

        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 6)

    def test_empty_inputs_give_an_empty_table_with_the_contracts_columns(self) -> None:
        table = listing_events(
            duckdb.connect(), WALK_SCHEMA.empty_table(), OBSERVATION_SCHEMA.empty_table()
        )

        self.assertEqual(table.num_rows, 0)
        self.assertEqual(table.schema, EVENT_SCHEMA)


def landing_walk(run: str, n: int) -> dict:
    started = datetime.strptime(run, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return {
        "source": "syn",
        "run_id": run,
        "area": AREA,
        "run_started_at": started,
        "run_ended_at": started + timedelta(minutes=20),
        "observed_date": day(n),
        "complete": True,
        "is_replay": False,
        "is_replay_source": "recorded",
    }


def landing_card(run: str, n: int, listing_id: str) -> dict:
    observed_at = datetime.strptime(run, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) + timedelta(
        minutes=5
    )
    return {
        "source": "syn",
        "run_id": run,
        "area": AREA,
        "operation": "sale",
        "municipality": "alba",
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": day(n),
        "ingested_at": observed_at,
        "price_eur": 250_000,
        "is_empty_card": False,
    }


def landing_detail(run: str, n: int, listing_id: str) -> dict[str, list[dict]]:
    card = landing_card(run, n, listing_id)
    key = {
        name: card[name]
        for name in (
            "source",
            "run_id",
            "area",
            "source_listing_id",
            "observed_at",
            "observed_date",
            "ingested_at",
        )
    }
    return {
        "listing_detail": [
            key | {"operation": "sale", "municipality": "alba", "price_eur": 250_000}
        ],
        "listing_text": [key | {"title": "Piso", "description": "Luminoso.", "features": []}],
    }


class TestListingEventInTheRun(unittest.TestCase):
    """The table on a catalog, maintained by process_landing from hand-written landing."""

    DAY_1, DAY_3, LATE_DAY_3 = "20261001T060000Z", "20261003T060000Z", "20261003T120000Z"

    def setUp(self) -> None:
        self.landing = Path(self.enterContext(tempfile.TemporaryDirectory()))
        write_landing_v3(
            self.landing,
            {
                "walk": [landing_walk(self.DAY_1, 1), landing_walk(self.DAY_3, 3)],
                "observation": [
                    landing_card(self.DAY_1, 1, "L1"),
                    landing_card(self.DAY_1, 1, "L2"),
                ],
            }
            | landing_detail(self.DAY_1, 1, "L1"),
        )

    def catalog(self) -> SqlCatalog:
        warehouse = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return SqlCatalog(
            "test", uri=f"sqlite:///{warehouse}/catalog.db", warehouse=f"file://{warehouse}"
        )

    def process(self, catalog: SqlCatalog, mode: str = "incremental") -> None:
        connection = duckdb.connect()
        self.addCleanup(connection.close)
        process_landing(
            connection,
            catalog,
            landing=str(self.landing),
            mode=mode,
            list_commits=lambda: local_commits(self.landing),
            now=lambda: datetime(2026, 10, 20, tzinfo=UTC),
        )

    def events(self, catalog: SqlCatalog) -> list[tuple]:
        rows = catalog.load_table(f"{NAMESPACE}.listing_event").scan().to_arrow().to_pylist()
        return sorted(
            (row["source_listing_id"], row["event_type"], row["event_date"]) for row in rows
        )

    def test_a_late_run_that_sees_the_listing_retracts_its_disappearance(self) -> None:
        catalog = self.catalog()
        self.process(catalog)
        self.assertEqual(
            [("L1", "disappeared", day(3)), ("L2", "disappeared", day(3))], self.events(catalog)
        )

        write_landing_v3(
            self.landing,
            {
                "walk": [landing_walk(self.LATE_DAY_3, 3)],
                "observation": [landing_card(self.LATE_DAY_3, 3, "L1")],
            },
        )
        self.process(catalog)

        backfilled = self.catalog()
        self.process(backfilled, mode="backfill")
        self.assertEqual([("L2", "disappeared", day(3))], self.events(catalog))
        self.assertEqual(self.events(backfilled), self.events(catalog))
