"""Write landing tables that conform to the contract, from hand-written or generated rows.

Landing v3 (one partition and one commit per collection run) is what Silver reads. The v1
writer remains only until the last FEATURE-001 test has moved to v3.
"""

import json
import random
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from src.contracts import landing_v1_columns, landing_v3_columns, landing_v3_sha256

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


# --- Landing v3 ---------------------------------------------------------------------------------

RunMetadata = Mapping[str, object]


def write_landing_v3(
    root: Path,
    rows_by_table: Mapping[str, Sequence[LandingRow]],
    runs: Mapping[str, RunMetadata] | None = None,
) -> None:
    """Write a landing v3 tree under ``root``: per run, one file per table and one commit.

    Every row carries its ``run_id``. Every table gets a file for every run, a zero-row file where
    the run has no rows, so the tree is whole. ``runs`` may set a run's ``run_started_at``,
    ``outcome`` and ``is_replay``; by default the start is read from the run id (``import-<date>``
    starts at that midnight), the outcome is ``ok`` and the run is live. Each commit is true of
    its files, and its ``written_at`` is derived from the start, so a seed writes the same tree.
    """
    contract = landing_v3_columns()
    unknown_tables = sorted(set(rows_by_table) - set(contract))
    if unknown_tables:
        raise ValueError(f"Not landing v3 tables: {', '.join(unknown_tables)}")
    for table, rows in rows_by_table.items():
        if any(not row.get("run_id") for row in rows):
            raise ValueError(f"Landing v3 table {table} has a row without run_id")
    runs = runs or {}
    run_ids = sorted({row["run_id"] for rows in rows_by_table.values() for row in rows} | set(runs))

    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    counts: dict[str, dict[str, int]] = {run_id: {} for run_id in run_ids}
    days: dict[str, dict[str, list[str]]] = {run_id: {} for run_id in run_ids}
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
        for run_id in run_ids:
            started = _run_started_at(run_id, runs.get(run_id, {}))
            target = root / table / f"run_date={started.date().isoformat()}" / f"run={run_id}"
            target.mkdir(parents=True, exist_ok=True)
            selection = f"SELECT * FROM \"{table}\" WHERE run_id = '{run_id}'"
            connection.execute(
                f"COPY ({selection}) TO '{target / 'part-0.parquet'}' (FORMAT parquet)"
            )
            count, observed = connection.execute(
                f"SELECT count(*), list(DISTINCT observed_date::VARCHAR ORDER BY 1) "
                f"FROM ({selection})"
            ).fetchone()
            counts[run_id][table] = count
            days[run_id][table] = [day for day in (observed or []) if day]
    connection.close()

    commits = root / "_commits"
    commits.mkdir(parents=True, exist_ok=True)
    for run_id in run_ids:
        metadata = runs.get(run_id, {})
        started = _run_started_at(run_id, metadata)
        commit = {
            "contract": "landing",
            "version": "v3",
            "schema_sha256": landing_v3_sha256(),
            "run_id": run_id,
            "run_started_at": _instant(started),
            "outcome": metadata.get("outcome", "ok"),
            "is_replay": bool(metadata.get("is_replay", False)),
            "row_counts": counts[run_id],
            "observed_dates": days[run_id],
            "written_at": _instant(started + timedelta(hours=3)),
        }
        (commits / f"{run_id}.json").write_text(json.dumps(commit, indent=2) + "\n")


def _run_started_at(run_id: str, metadata: RunMetadata) -> datetime:
    if "run_started_at" in metadata:
        return metadata["run_started_at"]
    if run_id.startswith("import-"):
        day = datetime.strptime(run_id.removeprefix("import-"), "%Y%m%d")
        return day.replace(tzinfo=UTC)
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id(started: datetime) -> str:
    return started.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


LEGACY_IMPORT = "import-20261001"


def generate_landing_v3(root: Path, *, seed: int) -> None:
    """Write a whole synthetic landing v3: v1's hard cases as runs, plus the cases runs add.

    A walk row belongs to the run its ``run_started_at`` names (a legacy walk without one to an
    import); any other row belongs to its day's live 06:00 run unless its scenario names a run.
    The run-level cases: two live runs on one day, a replay re-deriving an old day with a newer
    ``ingested_at``, an import run without reports, a failed run, a run without detail records,
    and a listing whose chosen detail record lies in an earlier run than its latest card.
    """
    scenarios = (
        _recorded_replay(),
        _inferred_replay(),
        _legacy_walk(),
        _run_across_midnight_v3(),
        _lone_empty_card(),
        _priced_and_padding_copies(),
        _padding_copies_only(),
        _detail_on_two_days(),
        _tied_detail_records(),
        _observation_only(),
        _personal_data_in_description(),
        _filler(random.Random(seed)),
        _two_live_runs_on_one_day(),
        _replay_rederiving_an_old_day(),
        _import_without_reports(),
        _failed_run(),
        _detail_older_than_latest_card(),
    )
    rows: dict[str, list[LandingRow]] = {table: [] for table in landing_v3_columns()}
    for scenario in scenarios:
        for table, table_rows in scenario.items():
            rows[table].extend(_with_run(table, row) for row in table_rows)
    runs: dict[str, RunMetadata] = {
        _run_id(_at(FIRST_DAY + timedelta(days=2), 15)): {"is_replay": True},
        _run_id(_at(FIRST_DAY + timedelta(days=3), 15)): {"is_replay": True},
        REPLAY_RUN: {"is_replay": True},
        FAILED_RUN: {"outcome": "failed"},
    }
    write_landing_v3(root, rows, runs)


def _with_run(table: str, row: LandingRow) -> LandingRow:
    if row.get("run_id"):
        return row
    if table == "walk":
        started = row["run_started_at"]
        return dict(row) | {"run_id": _run_id(started) if started else LEGACY_IMPORT}
    return dict(row) | {"run_id": _run_id(_at(row["observed_date"], 6))}


def _in_run(run_id: str, part: Mapping[str, Sequence[LandingRow]]) -> dict[str, list[LandingRow]]:
    return {table: [dict(row) | {"run_id": run_id} for row in rows] for table, rows in part.items()}


def _run_across_midnight_v3() -> dict[str, list[LandingRow]]:
    # The card of the midnight run's second area is dated the next day but belongs to that run.
    part = _run_across_midnight()
    run_id = _run_id(part["walk"][0]["run_started_at"])
    return part | {"observation": [dict(card) | {"run_id": run_id} for card in part["observation"]]}


TWO_RUNS_DAY = FIRST_DAY + timedelta(days=2)
SECOND_LIVE_RUN = _run_id(_at(TWO_RUNS_DAY, 12))
REPLAY_RUN = _run_id(_at(FIRST_DAY + timedelta(days=4), 15))
FAILED_RUN = _run_id(_at(FIRST_DAY + timedelta(days=5), 12))
IMPORT_RUN = "import-20260930"


def _two_live_runs_on_one_day() -> dict[str, list[LandingRow]]:
    # The same listing in the same area on one day, once per live run; the later card is newer.
    # The second run reads no detail page, so its listing_detail and listing_text files are empty.
    second = _at(TWO_RUNS_DAY, 12)
    return _merge(
        {"observation": [_card("syn-two-runs", "alba", _at(TWO_RUNS_DAY, 7), price_eur=250_000)]},
        _in_run(
            SECOND_LIVE_RUN,
            {
                "walk": [_walk("alba", second, second + timedelta(minutes=10), TWO_RUNS_DAY)],
                "observation": [
                    _card("syn-two-runs", "alba", second + timedelta(minutes=5), price_eur=240_000)
                ],
            },
        ),
    )


def _replay_rederiving_an_old_day() -> dict[str, list[LandingRow]]:
    # A replay re-derives a stored detail page: same observed_at, newer ingested_at, a corrected
    # price. The newest-sighting rule must keep the replay's row.
    observed = _at(FIRST_DAY + timedelta(days=1), 7, 30)
    replayed = _detail("syn-rederived", "alba", observed, price_eur=275_000)
    for table_rows in replayed.values():
        for row in table_rows:
            row["ingested_at"] = _at(FIRST_DAY + timedelta(days=4), 15, 30)
    return _merge(
        {"observation": [_card("syn-rederived", "alba", observed)]},
        _detail("syn-rederived", "alba", observed, price_eur=270_000),
        _in_run(REPLAY_RUN, replayed),
    )


def _import_without_reports() -> dict[str, list[LandingRow]]:
    observed = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    return _in_run(
        IMPORT_RUN,
        _merge(
            {"observation": [_card("syn-imported", "cerro", observed)]},
            _detail("syn-imported", "cerro", observed),
        ),
    )


def _failed_run() -> dict[str, list[LandingRow]]:
    started = _at(FIRST_DAY + timedelta(days=5), 12)
    return _in_run(
        FAILED_RUN,
        {
            "walk": [
                _walk(
                    "brisa", started, started + timedelta(minutes=3), started.date(), complete=False
                )
            ],
            "observation": [_card("syn-failed-run", "brisa", started + timedelta(minutes=1))],
        },
    )


def _detail_older_than_latest_card() -> dict[str, list[LandingRow]]:
    first, later = _at(FIRST_DAY, 7, 15), _at(FIRST_DAY + timedelta(days=3), 7, 15)
    return _merge(
        {
            "observation": [
                _card("syn-early-detail", "duna", first),
                _card("syn-early-detail", "duna", later),
            ]
        },
        _detail("syn-early-detail", "duna", first),
    )
