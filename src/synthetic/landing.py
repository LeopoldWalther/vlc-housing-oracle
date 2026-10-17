"""Write landing tables that conform to the contract, from hand-written or generated rows."""

import random
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from src.contracts import landing_v1_columns

LandingRow = Mapping[str, object]


def write_landing_tables(
    directory: Path, rows_by_table: Mapping[str, Sequence[LandingRow]]
) -> None:
    """Write every contract table as ``<directory>/<table>.parquet``.

    A table missing from ``rows_by_table`` is written empty, so the directory always holds a whole
    landing. A column missing from a row is NULL. Every column is typed by the contract, never
    inferred, so an all-null column keeps its type.
    """
    contract = landing_v1_columns()
    unknown_tables = sorted(set(rows_by_table) - set(contract))
    if unknown_tables:
        raise ValueError(f"Not landing v1 tables: {', '.join(unknown_tables)}")

    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    for table, columns in contract.items():
        rows = rows_by_table.get(table, [])
        names = [name for name, _ in columns]
        unknown_columns = sorted({key for row in rows for key in row} - set(names))
        if unknown_columns:
            raise ValueError(f"Not columns of landing table {table}: {', '.join(unknown_columns)}")

        column_list = ", ".join(f'"{name}" {column_type}' for name, column_type in columns)
        connection.execute(f'CREATE TABLE "{table}" ({column_list})')
        if rows:
            placeholders = ", ".join("?" for _ in names)
            connection.executemany(
                f'INSERT INTO "{table}" VALUES ({placeholders})',
                [[row.get(name) for name in names] for row in rows],
            )
        target = directory / f"{table}.parquet"
        connection.execute(f"COPY \"{table}\" TO '{target}' (FORMAT parquet)")
    connection.close()


# Invented places: no municipality or district here exists in the real data's area list.
MUNICIPALITIES = ("alba", "brisa", "cerro", "duna")
FIRST_DAY = date(2026, 10, 1)
DAYS = 7
FILLER_LISTINGS = 24


def generate_landing(directory: Path, *, seed: int) -> None:
    """Write a whole synthetic landing: named hard cases plus filler drawn from ``seed``.

    The hard cases are the shapes Silver must handle: replays, legacy walks, a run across midnight,
    padding cards, ties between detail records, and descriptions holding personal data. The same
    seed always writes the same rows.
    """
    scenarios = (
        _recorded_replay(),
        _inferred_replay(),
        _legacy_walk(),
        _run_across_midnight(),
        _lone_empty_card(),
        _priced_and_padding_copies(),
        _padding_copies_only(),
        _detail_on_two_days(),
        _tied_detail_records(),
        _observation_only(),
        _personal_data_in_description(),
        _filler(random.Random(seed)),
    )
    rows: dict[str, list[LandingRow]] = {table: [] for table in landing_v1_columns()}
    for scenario in scenarios:
        for table, table_rows in scenario.items():
            rows[table].extend(table_rows)
    write_landing_tables(directory, rows)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _area(municipality: str, operation: str = "sale") -> dict[str, object]:
    return {
        "source": "syn",
        "area": f"{municipality}-{operation}",
        "operation": operation,
        "municipality": municipality,
        "district": None,
    }


def _walk(
    municipality: str,
    started_at: datetime | None,
    ended_at: datetime | None,
    day: date,
    **overrides: object,
) -> LandingRow:
    area = _area(municipality)
    return {
        "source": area["source"],
        "area": area["area"],
        "run_started_at": started_at,
        "run_ended_at": ended_at,
        "observed_date": day,
        "complete": True,
        "is_replay": False,
        "is_replay_source": "recorded",
        "pages": 3,
        "result_count": 60,
        "duration_seconds": 600.0,
    } | overrides


def _card(
    listing_id: str,
    municipality: str,
    observed_at: datetime,
    *,
    price_eur: int | None = 250_000,
    is_empty_card: bool = False,
) -> LandingRow:
    filled = not is_empty_card
    return _area(municipality) | {
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
        "ingested_at": observed_at + timedelta(hours=1),
        "price_eur": price_eur if filled else None,
        "sqm_built": 80 if filled else None,
        "rooms": 3 if filled else None,
        "freshness_label": "nuevo" if filled else None,
        "property_type": "piso",
        "is_empty_card": is_empty_card,
    }


def _detail(
    listing_id: str,
    municipality: str,
    observed_at: datetime,
    *,
    price_eur: int = 250_000,
    description: str = "Piso luminoso con balcón.",
) -> dict[str, list[LandingRow]]:
    """One detail record and its text row, keyed alike as the contract requires."""
    key = _area(municipality) | {
        "source_listing_id": listing_id,
        "observed_at": observed_at,
        "observed_date": observed_at.date(),
        "ingested_at": observed_at + timedelta(hours=1),
    }
    detail = key | {
        "extraction_version": "syn-1",
        "property_type": "piso",
        "price_eur": price_eur,
        "sqm_built": 80,
        "rooms": 3,
        "bathrooms": 1,
        "has_elevator_state": "true",
        "has_terrace_state": "false_stated",
        "has_pool_state": "unknown",
    }
    text = {
        name: key[name]
        for name in ("source", "area", "source_listing_id", "observed_at", "observed_date")
    } | {
        "ingested_at": key["ingested_at"],
        "title": "Piso en venta",
        "location": f"Calle Inventada, {municipality.title()}",
        "description": description,
        "features": ["80 m²", "3 habitaciones"],
    }
    return {"listing_detail": [detail], "listing_text": [text]}


def _merge(*parts: Mapping[str, Sequence[LandingRow]]) -> dict[str, list[LandingRow]]:
    merged: dict[str, list[LandingRow]] = {}
    for part in parts:
        for table, rows in part.items():
            merged.setdefault(table, []).extend(rows)
    return merged


def _recorded_replay() -> dict[str, list[LandingRow]]:
    day = FIRST_DAY + timedelta(days=2)
    start = _at(day, 15)
    return {"walk": [_walk("alba", start, start + timedelta(minutes=10), day, is_replay=True)]}


def _inferred_replay() -> dict[str, list[LandingRow]]:
    day = FIRST_DAY + timedelta(days=3)
    start = _at(day, 15)
    return {
        "walk": [
            _walk(
                "brisa",
                start,
                start + timedelta(minutes=10),
                day,
                is_replay=True,
                is_replay_source="inferred",
            )
        ]
    }


def _legacy_walk() -> dict[str, list[LandingRow]]:
    return {
        "walk": [
            _walk(
                "cerro",
                None,
                None,
                FIRST_DAY,
                complete=None,
                is_replay=None,
                is_replay_source=None,
            )
        ]
    }


def _run_across_midnight() -> dict[str, list[LandingRow]]:
    # The second area's own start plus its duration stays before midnight, but the run does not:
    # only run_ended_at shows the walk is not inside one UTC day.
    day = FIRST_DAY + timedelta(days=4)
    start = _at(day, 23, 20)
    end = start + timedelta(minutes=70)
    return {
        "walk": [
            _walk("alba", start, end, day, duration_seconds=3000.0),
            _walk("duna", start, end, day, duration_seconds=1200.0),
        ],
        "observation": [_card("syn-midnight-1", "duna", _at(day + timedelta(days=1), 0, 5))],
    }


def _lone_empty_card() -> dict[str, list[LandingRow]]:
    return {
        "observation": [_card("syn-padding-lone", "brisa", _at(FIRST_DAY, 9), is_empty_card=True)]
    }


def _priced_and_padding_copies() -> dict[str, list[LandingRow]]:
    at = _at(FIRST_DAY, 9)
    return {
        "observation": [
            _card("syn-padded", "alba", at, is_empty_card=True),
            _card("syn-padded", "cerro", at, price_eur=199_000),
        ]
    }


def _padding_copies_only() -> dict[str, list[LandingRow]]:
    at = _at(FIRST_DAY, 9)
    return {
        "observation": [
            _card("syn-padding-twice", "duna", at, is_empty_card=True),
            _card("syn-padding-twice", "brisa", at, is_empty_card=True),
        ]
    }


def _detail_on_two_days() -> dict[str, list[LandingRow]]:
    first, later = _at(FIRST_DAY, 9), _at(FIRST_DAY + timedelta(days=5), 9)
    return _merge(
        {
            "observation": [
                _card("syn-repriced", "alba", first),
                _card("syn-repriced", "alba", later),
            ]
        },
        _detail("syn-repriced", "alba", first, price_eur=260_000),
        _detail("syn-repriced", "alba", later, price_eur=245_000),
    )


def _tied_detail_records() -> dict[str, list[LandingRow]]:
    at = _at(FIRST_DAY + timedelta(days=1), 9)
    return _merge(
        {"observation": [_card("syn-tied", "cerro", at)]},
        _detail("syn-tied", "cerro", at, price_eur=310_000),
        _detail("syn-tied", "brisa", at, price_eur=305_000),
    )


def _observation_only() -> dict[str, list[LandingRow]]:
    return {"observation": [_card("syn-no-detail", "duna", _at(FIRST_DAY, 10))]}


def _personal_data_in_description() -> dict[str, list[LandingRow]]:
    at = _at(FIRST_DAY + timedelta(days=1), 11)
    return _merge(
        {"observation": [_card("syn-contact", "alba", at)]},
        _detail(
            "syn-contact",
            "alba",
            at,
            description="Llame al 600 000 000 o al +34 700 000 000, pregunte por Sra. García.",
        ),
    )


def _filler(rng: random.Random) -> dict[str, list[LandingRow]]:
    """One live, complete run per day over every area, and listings seen on random days."""
    parts: list[Mapping[str, Sequence[LandingRow]]] = []
    for offset in range(DAYS):
        day = FIRST_DAY + timedelta(days=offset)
        start = _at(day, 6)
        durations = [float(rng.randint(300, 900)) for _ in MUNICIPALITIES]
        end = start + timedelta(seconds=sum(durations))
        parts.append(
            {
                "walk": [
                    _walk(municipality, start, end, day, duration_seconds=duration)
                    for municipality, duration in zip(MUNICIPALITIES, durations, strict=True)
                ]
            }
        )
    for number in range(1, FILLER_LISTINGS + 1):
        listing_id = f"syn-{number:04d}"
        municipality = rng.choice(MUNICIPALITIES)
        price = rng.randrange(120_000, 600_000, 1_000)
        days = sorted(rng.sample(range(DAYS), rng.randint(1, DAYS)))
        cards = [
            _card(listing_id, municipality, _at(FIRST_DAY + timedelta(days=d), 7), price_eur=price)
            for d in days
        ]
        parts.append({"observation": cards})
        if rng.random() < 0.5:
            parts.append(
                _detail(listing_id, municipality, cards[0]["observed_at"], price_eur=price)
            )
    return _merge(*parts)
