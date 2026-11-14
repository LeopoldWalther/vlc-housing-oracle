"""listing_event: a listing disappears at the first complete live walk of its area that misses it."""

import unittest
from datetime import UTC, date, datetime, timedelta

import duckdb
import pyarrow as pa

from src.silver.rebuild import listing_events

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
