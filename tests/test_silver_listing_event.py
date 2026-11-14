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


if __name__ == "__main__":
    unittest.main()
