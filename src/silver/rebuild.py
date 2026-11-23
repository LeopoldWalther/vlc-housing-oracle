"""Maintain Silver from landing v3: DuckDB SQL computes the tables, Iceberg stores them.

Each run processes the landing runs Silver has not ingested yet (ADR 0003). Run from the
repository root, or as the Fargate task's entry point::

    python -m src.silver.rebuild --landing <dir or s3://bucket/landing/v3> --catalog <name> \
        [--mode incremental|backfill] [--metrics] [--census-sections <vintage dir or s3://...>]
"""

import argparse
import json
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.expressions import In

from src.contracts import landing_v3_sha256
from src.enrichment.prompt import LABEL_VERSION
from src.enrichment.text_labels import StoredAnswerError, stored_answers
from src.silver.affected import ListingKey, affected_listings
from src.silver.commits import LandingCommit, days_of, parse_commit, pending, runs_for
from src.silver.feature_patterns import LABEL_TARGETS, UNREAD, register_feature_registry
from src.silver.ingest import IngestedRun, ingested_runs, record_ingested
from src.silver.landing import LandingContractError, read_defect, register_landing_v3
from src.silver.property_group import (
    PROPERTY_GROUP_LISTING_COLUMNS,
    PROPERTY_GROUP_TABLE,
    duplicate_pairs,
    property_group_rows,
)
from src.silver.sink import SilverSchemaError, replace_days, upsert_rows, write_silver_tables

SQL_DIR = Path(__file__).resolve().parent / "sql"

NAMESPACE = "vlc_housing_silver"
TRANSFORMATION_VERSION = "silver-v1"
REDACTION_VERSION = "r2"

# In dependency order: a statement may read the silver_<table> of any table listed before it.
SILVER_TABLES = (
    "walk",
    "observation",
    "listing_detail",
    "listing",
    "listing_quality",
    "listing_text",
    "feature_pattern",
    "listing_feature_label",
    "listing_event",
    "listing_price",
)

# The columns of Silver listing that the listing_quality rules read.
QUALITY_LISTING_COLUMNS = (
    "source",
    "source_listing_id",
    "detail_observed_at",
    "operation",
    "municipality",
    "price_eur",
    "prev_price_eur",
    "sqm_built",
    "sqm_usable",
    "rooms",
    "bathrooms",
    "year_built",
    "latitude",
    "longitude",
)

# The columns of Silver listing that the label rules read: quality flags and property groups.
LABEL_LISTING_COLUMNS = (
    *QUALITY_LISTING_COLUMNS,
    *(name for name in PROPERTY_GROUP_LISTING_COLUMNS if name not in QUALITY_LISTING_COLUMNS),
)

# The columns of Silver listing_text that the feature rules read.
FEATURE_TEXT_COLUMNS = ("source", "source_listing_id", "features")

# Written only by a run given a census-section vintage, so it is no member of SILVER_TABLES.
LOCATION_TABLE = "listing_location"

# The columns of Silver listing that the listing_location rule reads.
LOCATION_LISTING_COLUMNS = (
    "source",
    "source_listing_id",
    "detail_observed_at",
    "latitude",
    "longitude",
)

# Written only by a run given the stored answers of the text labeller (ADR 0005), so it is no
# member of SILVER_TABLES.
TEXT_LABEL_TABLE = "listing_text_label"

# The columns of Silver listing and listing_text that the listing_text_label rule reads.
TEXT_LABEL_LISTING_COLUMNS = ("source", "source_listing_id", "operation")
TEXT_LABEL_TEXT_COLUMNS = ("source", "source_listing_id", "description")

# The columns of one census-section vintage (ADR 0004), in the file's order.
CENSUS_SECTION_COLUMNS = (
    "section_code",
    "district_code",
    "municipality_code",
    "municipality_name",
    "geometry",
    "vintage",
)

# Thresholds of the listing_quality rules, from docs/data-quality/silver-quality-2026-11.md.
# Prices are EUR for sale and EUR per month for rent; areas are m².
QUALITY_THRESHOLDS = {
    "sale_price_min": 10_000,
    "sale_price_max": 20_000_000,
    "rent_price_min": 100,
    "rent_price_max": 30_000,
    "sqm_built_min": 10,
    "sqm_built_max": 2_000,
    "sale_price_per_sqm_min": 300,
    "sale_price_per_sqm_max": 15_000,
    "rent_price_per_sqm_min": 2,
    "rent_price_per_sqm_max": 60,
    "rooms_max": 12,
    "bathrooms_over_rooms_max": 2,
    "year_built_min": 1500,
    "year_built_ahead_max": 1,
    "municipality_distance_max_km": 10,
}

# Intermediate views shared by several Silver statements, created before them.
SHARED_VIEWS = ("chosen_detail",)

# Listings are upserted on this key in listing and listing_text.
LISTING_KEY = ["source", "source_listing_id"]


# The tables replaced per day; listing and listing_text are recomputed per affected listing.
DAY_TABLES = ("walk", "observation", "listing_detail")

# The columns of Silver walk and observation that the listing_event rule reads.
EVENT_WALK_COLUMNS = (
    "source",
    "area",
    "observed_date",
    "run_id",
    "run_started_at",
    "run_ended_at",
    "complete",
    "is_replay",
)
EVENT_OBSERVATION_COLUMNS = (
    "source",
    "source_listing_id",
    "observed_date",
    "area",
    "is_empty_card",
    "run_id",
)

# The columns of Silver observation and listing_detail that the listing_price rule reads.
PRICE_OBSERVATION_COLUMNS = (
    "source",
    "source_listing_id",
    "observed_at",
    "price_eur",
    "prev_price_eur",
    "is_empty_card",
)
PRICE_DETAIL_COLUMNS = (
    "source",
    "source_listing_id",
    "observed_at",
    "price_eur",
    "prev_price_eur",
)


class ReferenceDataError(ValueError):
    """A reference vintage cannot be read or fails its check; the message holds no row value."""


class BackfillRequired(Exception):
    """Silver's tables were written by another rule or redaction version than this code's."""


def require_current_versions(catalog: Catalog, ingested: Mapping[str, IngestedRun]) -> None:
    """Refuse an incremental run over tables that another version of the rules wrote.

    A run of the code's version would rewrite only the days and listings its commits touch and
    leave the rest of history under the old version. A completed backfill rewrites every row of
    the ingest log, so the log tells whether the rules of every table are the code's. Redacted
    text lives in ``listing_text`` only, and every write of it records the redaction version in
    its snapshot summary, so its current snapshot tells whether its text is the code's.
    """
    found = sorted({run.transformation_version for run in ingested.values()})
    if found and found != [TRANSFORMATION_VERSION]:
        raise BackfillRequired(
            f"the ingest log holds transformation version {', '.join(found)}, this code is "
            f"{TRANSFORMATION_VERSION}: run --mode backfill"
        )
    identifier = f"{NAMESPACE}.listing_text"
    if not catalog.table_exists(identifier):
        return
    snapshot = catalog.load_table(identifier).current_snapshot()
    if snapshot is None:
        return
    redaction = snapshot.summary.get("redaction_version") if snapshot.summary else None
    if redaction != REDACTION_VERSION:
        raise BackfillRequired(
            f"listing_text holds redaction version {redaction or 'unknown'}, this code is "
            f"{REDACTION_VERSION}: run --mode backfill"
        )


@dataclass(frozen=True)
class ProcessResult:
    """What one incremental or backfill run did."""

    pending: int
    processed: tuple[str, ...]
    days: dict[str, tuple[date, ...]]
    affected: int
    snapshot_ids: dict[str, int | None]
    # Feature wordings of this run's Silver that are not in the registry.
    unknown_feature_patterns: int


def local_commits(landing: Path) -> list[LandingCommit]:
    """Every commit of a local landing v3 tree."""
    return [
        parse_commit(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((landing / "_commits").glob("*.json"))
    ]


def process_landing(
    connection: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    *,
    landing: str,
    mode: str,
    list_commits: Callable[[], list[LandingCommit]],
    now: Callable[[], datetime],
    census_sections: str | None = None,
    text_labels: str | None = None,
) -> ProcessResult:
    """Process the landing runs Silver has not ingested yet; ``backfill`` treats every run as new.

    Pending runs are landing's commits minus the ingest log. The days they touch are rebuilt from
    every run that touches them, the listings those days held before or hold now are recomputed
    from Silver's history, and the ingest log is written last: a run that stops before it leaves
    its runs pending, and repeating them gives the same rows.

    An incremental run over tables that another rule or redaction version wrote raises
    ``BackfillRequired`` before it reads landing, so it writes nothing; a backfill is the way to
    the new version.

    ``census_sections`` names one reference vintage directory. A run given it also writes
    ``listing_location``; the vintage is read and checked before the run's first write, so a
    ``ReferenceDataError`` leaves every table as it was and the runs pending.

    ``text_labels`` names the labeller's stored answers, a directory or s3:// location. A run
    given it also writes ``listing_text_label``; the answers are read before the run's first
    write, so a ``StoredAnswerError`` leaves every table as it was.
    """
    if mode not in ("incremental", "backfill"):
        raise ValueError(f"unknown mode {mode!r}")
    backfill = mode == "backfill"
    commits = list_commits()
    ingested = {} if backfill else ingested_runs(catalog, NAMESPACE)
    if not backfill:
        require_current_versions(catalog, ingested)
    todo = pending(commits, set(ingested))
    if not todo:
        return ProcessResult(0, (), {}, 0, {}, 0)
    sections = (
        None if census_sections is None else read_census_sections(connection, census_sections)
    )
    answers = None if text_labels is None else stored_answers(connection, text_labels)

    known = {run_id: run.observed_dates for run_id, run in ingested.items()}
    known |= {commit.run_id: commit.observed_dates for commit in todo}
    run_started = {run_id: run.run_started_at for run_id, run in ingested.items()}
    run_started |= {commit.run_id: commit.run_started_at for commit in todo}
    days = days_of(todo)
    register_landing_v3(
        connection,
        landing,
        runs_for(days, known),
        run_started,
        {commit.run_id: commit.row_counts for commit in todo},
        expected_dates={commit.run_id: commit.observed_dates for commit in todo},
    )
    tables = build_tables(connection)
    properties = _properties(todo, now)

    previous: list[pa.Table] = []
    new: list[pa.Table] = []
    snapshot_ids: dict[str, int | None] = {}
    for name in DAY_TABLES:
        named = days[name]
        rows = tables[name].filter(
            pc.is_in(tables[name]["observed_date"], pa.array(sorted(named), pa.date32()))
        )
        if name in ("observation", "listing_detail"):
            previous.append(_silver_rows_of_days(catalog, name, named))
            new.append(rows)
        snapshot_ids[name] = replace_days(
            catalog, NAMESPACE, name, rows, named, properties, allow_new_columns=backfill
        )

    affected = set().union(*(affected_listings(p, n) for p, n in zip(previous, new, strict=True)))
    if backfill:
        # Every listing is affected, so the whole tables are written: this is also the only path
        # that may add a column to them (an upsert cannot, on files that predate it).
        listing, listing_text = _compute_listings(
            connection, catalog, landing=landing, affected=affected, run_started=run_started
        )
        snapshot_ids |= write_silver_tables(
            catalog,
            NAMESPACE,
            {"listing": listing, "listing_text": listing_text},
            properties,
            allow_new_columns=True,
        )
    else:
        snapshot_ids |= update_listings(
            connection,
            catalog,
            landing=landing,
            affected=affected,
            run_started=run_started,
            properties=properties,
        )
    # A run can retract or add events of any listing, add a sighting to any listing's price
    # history, move a municipality's centre and with it the flag of any listing, join or split
    # property groups of listings it did not touch, and change any listing's features and with
    # them the counts of any wording, so these tables are recomputed and replaced whole; they
    # read Silver after this run's days and listings are written. So is listing_location, whose
    # polygons can change with the vintage the run is given, and listing_text_label, whose
    # answers can change with every labeller run.
    labels = _labels_from_silver(connection, catalog, sections, answers)
    snapshot_ids |= write_silver_tables(
        catalog,
        NAMESPACE,
        {**labels, **_history_from_silver(connection, catalog)},
        properties,
        allow_new_columns=backfill,
    )
    record_ingested(catalog, NAMESPACE, todo, now(), TRANSFORMATION_VERSION)
    return ProcessResult(
        pending=len(todo),
        processed=tuple(commit.run_id for commit in todo),
        days={name: tuple(sorted(days[name])) for name in DAY_TABLES},
        affected=len(affected),
        snapshot_ids=snapshot_ids,
        unknown_feature_patterns=labels["feature_pattern"]["target"].null_count,
    )


def _properties(todo: Sequence[LandingCommit], now: Callable[[], datetime]) -> dict[str, str]:
    # Bounded: the full list of runs lives in the ingest log, not in every snapshot summary.
    run_ids = sorted(commit.run_id for commit in todo)
    return {
        "transformation_version": TRANSFORMATION_VERSION,
        "redaction_version": REDACTION_VERSION,
        "landing_contract_sha256": landing_v3_sha256(),
        "landing_run_count": str(len(run_ids)),
        "landing_run_first": run_ids[0],
        "landing_run_last": run_ids[-1],
        "rebuild_started_at": now().astimezone(UTC).isoformat(),
    }


def _silver_rows_of_days(catalog: Catalog, name: str, days: set[date]) -> pa.Table:
    """Silver's current rows of the named days, read before they are replaced."""
    identifier = f"{NAMESPACE}.{name}"
    if not days or not catalog.table_exists(identifier):
        return pa.table(
            {"source": pa.array([], pa.string()), "source_listing_id": pa.array([], pa.string())}
        )
    scan = catalog.load_table(identifier).scan(
        row_filter=In("observed_date", sorted(day.isoformat() for day in days)),
        selected_fields=("source", "source_listing_id"),
    )
    return scan.to_arrow()


def build_tables(connection: duckdb.DuckDBPyConnection) -> dict[str, pa.Table]:
    """Compute every Silver table from the registered ``landing_<table>`` views.

    Each result is also kept as the temporary table ``silver_<table>`` for later statements. The
    session runs in UTC because DuckDB exports instants in the session zone and Iceberg accepts
    only UTC.
    """
    _prepare(connection)
    connection.execute(_statement("newest_sighting"))
    for name in SHARED_VIEWS:
        connection.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS {_statement(name)}")
    tables: dict[str, pa.Table] = {}
    for name in SILVER_TABLES:
        statement = _statement(name)
        connection.execute(f"CREATE OR REPLACE TEMP TABLE silver_{name} AS {statement}")
        tables[name] = connection.table(f"silver_{name}").to_arrow_table()
    return tables


def update_listings(
    connection: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    *,
    landing: str,
    affected: set[ListingKey],
    run_started: Mapping[str, datetime],
    properties: Mapping[str, str],
) -> dict[str, int | None]:
    """Recompute ``listing`` and ``listing_text`` for the affected listings and upsert them.

    History comes from Silver, not landing: the affected ids' rows of Silver ``observation`` and
    ``listing_detail`` are registered under the names FEATURE-001's statements read, so the rules
    run unchanged. Silver keeps no text, so the chosen records' text is read from landing, from
    exactly the runs that hold a chosen record. An affected listing with no rows left is deleted.
    Run this after the day partitions of the same run are replaced.
    """
    if not affected:
        return {}
    listing, listing_text = _compute_listings(
        connection, catalog, landing=landing, affected=affected, run_started=run_started
    )
    snapshot_ids: dict[str, int | None] = {}
    for name, rows in (("listing", listing), ("listing_text", listing_text)):
        present = set(
            zip(rows["source"].to_pylist(), rows["source_listing_id"].to_pylist(), strict=True)
        )
        snapshot_ids[name] = upsert_rows(
            catalog, NAMESPACE, name, rows, LISTING_KEY, affected - present, properties
        )
    return snapshot_ids


def _compute_listings(
    connection: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    *,
    landing: str,
    affected: set[ListingKey],
    run_started: Mapping[str, datetime],
) -> tuple[pa.Table, pa.Table]:
    """``listing`` and ``listing_text`` rows of the affected listings, from Silver's history."""
    _prepare(connection)
    keys = pa.table(
        {
            "source": [source for source, _ in sorted(affected)],
            "source_listing_id": [listing_id for _, listing_id in sorted(affected)],
        }
    )
    connection.register("affected_keys", keys)
    for name, view in (
        ("observation", "affected_observation"),
        ("listing_detail", "affected_detail"),
    ):
        scan = catalog.load_table(f"{NAMESPACE}.{name}").scan(
            row_filter=In("source_listing_id", sorted({lid for _, lid in affected}))
        )
        connection.register(view, scan.to_arrow())
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE silver_observation AS SELECT * FROM affected_observation "
        "SEMI JOIN affected_keys USING (source, source_listing_id)"
    )
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW newest_listing_detail AS "
        "SELECT * EXCLUDE (transformation_version) FROM affected_detail "
        "SEMI JOIN affected_keys USING (source, source_listing_id)"
    )
    connection.execute(
        f"CREATE OR REPLACE TEMP VIEW chosen_detail AS {_statement('chosen_detail')}"
    )
    listing = connection.sql(_statement("listing")).to_arrow_table()

    chosen_runs = {
        row[0] for row in connection.sql("SELECT DISTINCT run_id FROM chosen_detail").fetchall()
    }
    register_landing_v3(
        connection,
        landing,
        {"listing_text": chosen_runs},
        {run_id: run_started[run_id] for run_id in chosen_runs},
        {},
    )
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW newest_listing_text AS SELECT * FROM landing_listing_text"
    )
    listing_text = connection.sql(_statement("listing_text")).to_arrow_table()
    return listing, listing_text


def read_census_sections(connection: duckdb.DuckDBPyConnection, path: str) -> pa.Table:
    """One census-section vintage, read from its directory (local or ``s3://``) and checked.

    The file must be readable, hold the six columns and at least one row, and carry one vintage
    on every row. Anything else raises ``ReferenceDataError`` naming the path and the defect.
    """
    file = f"{path.rstrip('/')}/part-0.parquet"
    if "://" not in file and not Path(file).is_file():
        raise ReferenceDataError(f"census sections {file} has no file")
    # The directory is named vintage=<date>: without this the path would supply the column.
    source = "read_parquet(?, hive_partitioning = false)"
    try:
        described = connection.execute(f"DESCRIBE SELECT * FROM {source}", [file]).fetchall()
        present = {name for name, *_ in described}
        missing = [name for name in CENSUS_SECTION_COLUMNS if name not in present]
        if missing:
            raise ReferenceDataError(f"census sections {file} lack {', '.join(missing)}")
        sections = connection.execute(
            f"SELECT {', '.join(CENSUS_SECTION_COLUMNS)} FROM {source}", [file]
        ).to_arrow_table()
    except duckdb.Error as error:
        defect = read_defect(error)
        if defect is None:
            raise
        raise ReferenceDataError(f"census sections {file} {defect}") from error
    if sections.num_rows == 0:
        raise ReferenceDataError(f"census sections {file} hold no row")
    vintages = pc.count_distinct(sections["vintage"], mode="all").as_py()
    if vintages != 1 or sections["vintage"].null_count:
        raise ReferenceDataError(f"census sections {file} do not carry one vintage on every row")
    return sections


def _labels_from_silver(
    connection: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    sections: pa.Table | None,
    answers: pa.Table | None = None,
) -> dict[str, pa.Table]:
    """The label tables over one read of Silver's current ``listing`` and ``listing_text``.

    These are ``listing_quality``, the property groups, ``feature_pattern`` and
    ``listing_feature_label``, ``listing_location`` when ``sections`` are given and
    ``listing_text_label`` when ``answers`` are given.
    """
    # Read both before any rule: each rule replaces silver_listing or silver_listing_text, which
    # the reads fall back to while the catalog has no such table, with its own columns only.
    listing = _silver_columns(connection, catalog, "listing", None)
    text_columns = (
        FEATURE_TEXT_COLUMNS
        if answers is None
        else (
            *FEATURE_TEXT_COLUMNS,
            *(c for c in TEXT_LABEL_TEXT_COLUMNS if c not in FEATURE_TEXT_COLUMNS),
        )
    )
    texts = _silver_columns(connection, catalog, "listing_text", text_columns)
    listing_text = texts.select(list(FEATURE_TEXT_COLUMNS))
    grouped = listing.select(LABEL_LISTING_COLUMNS)
    located = (
        {}
        if sections is None
        else {
            LOCATION_TABLE: listing_locations(
                connection, listing.select(LOCATION_LISTING_COLUMNS), sections
            )
        }
    )
    described = (
        {}
        if answers is None
        else {
            TEXT_LABEL_TABLE: listing_text_labels(
                connection,
                listing.select(list(TEXT_LABEL_LISTING_COLUMNS)),
                texts.select(list(TEXT_LABEL_TEXT_COLUMNS)),
                answers,
            )
        }
    )
    return {
        "listing_quality": listing_quality(connection, listing.select(QUALITY_LISTING_COLUMNS)),
        PROPERTY_GROUP_TABLE: property_group_rows(
            grouped,
            duplicate_pairs(connection, grouped),
            transformation_version=TRANSFORMATION_VERSION,
        ),
        "feature_pattern": feature_patterns(connection, listing, listing_text),
        "listing_feature_label": listing_feature_labels(connection, listing_text),
        **located,
        **described,
    }


def _history_from_silver(
    connection: duckdb.DuckDBPyConnection, catalog: Catalog
) -> dict[str, pa.Table]:
    """``listing_event`` and ``listing_price`` over one read of Silver's current day tables."""
    # Read before either rule: each replaces temporary tables the read falls back to while the
    # catalog has no such table, with its own columns only.
    walk = _silver_columns(connection, catalog, "walk", EVENT_WALK_COLUMNS)
    observation = _silver_columns(
        connection,
        catalog,
        "observation",
        (
            *EVENT_OBSERVATION_COLUMNS,
            *(name for name in PRICE_OBSERVATION_COLUMNS if name not in EVENT_OBSERVATION_COLUMNS),
        ),
    )
    listing_detail = _silver_columns(connection, catalog, "listing_detail", PRICE_DETAIL_COLUMNS)
    return {
        "listing_event": listing_events(
            connection, walk, observation.select(EVENT_OBSERVATION_COLUMNS)
        ),
        "listing_price": listing_prices(
            connection, observation.select(PRICE_OBSERVATION_COLUMNS), listing_detail
        ),
    }


def _silver_columns(
    connection: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    name: str,
    columns: Sequence[str] | None,
) -> pa.Table:
    """Silver's current rows of one table; ``columns`` of None reads every column."""
    identifier = f"{NAMESPACE}.{name}"
    if catalog.table_exists(identifier):
        selected = ("*",) if columns is None else tuple(columns)
        return catalog.load_table(identifier).scan(selected_fields=selected).to_arrow()
    # A table appears with its first rows; until then this run's own result, empty as well,
    # gives the columns their types.
    return connection.sql(
        f"SELECT {'*' if columns is None else ', '.join(columns)} FROM silver_{name} LIMIT 0"
    ).to_arrow_table()


def listing_events(
    connection: duckdb.DuckDBPyConnection, walk: pa.Table, observation: pa.Table
) -> pa.Table:
    """The ``listing_event`` rows of every listing, from Silver's whole walk and observation.

    The given tables, which need ``EVENT_WALK_COLUMNS`` and ``EVENT_OBSERVATION_COLUMNS``, replace
    the temporary tables ``silver_walk`` and ``silver_observation``: call this after every other
    statement of the session.
    """
    _prepare(connection)
    for name, rows in (("walk", walk), ("observation", observation)):
        connection.register(f"event_{name}", rows)
        connection.execute(f"CREATE OR REPLACE TEMP TABLE silver_{name} AS FROM event_{name}")
        connection.unregister(f"event_{name}")
    return connection.sql(_statement("listing_event")).to_arrow_table()


def listing_prices(
    connection: duckdb.DuckDBPyConnection, observation: pa.Table, listing_detail: pa.Table
) -> pa.Table:
    """The ``listing_price`` rows of every listing, from Silver's whole observation and detail.

    The given tables, which need ``PRICE_OBSERVATION_COLUMNS`` and ``PRICE_DETAIL_COLUMNS``,
    replace the temporary tables ``silver_observation`` and ``silver_listing_detail``: call this
    after every other statement of the session that reads them.
    """
    _prepare(connection)
    for name, rows in (("observation", observation), ("listing_detail", listing_detail)):
        connection.register(f"price_{name}", rows)
        connection.execute(f"CREATE OR REPLACE TEMP TABLE silver_{name} AS FROM price_{name}")
        connection.unregister(f"price_{name}")
    return connection.sql(_statement("listing_price")).to_arrow_table()


def listing_quality(connection: duckdb.DuckDBPyConnection, listing: pa.Table) -> pa.Table:
    """The ``listing_quality`` rows of every listing with a detail record.

    The given table, which needs ``QUALITY_LISTING_COLUMNS``, replaces the temporary table
    ``silver_listing``: call this after every other statement of the session that reads it.
    """
    _prepare(connection)
    connection.register("quality_listing", listing)
    connection.execute("CREATE OR REPLACE TEMP TABLE silver_listing AS FROM quality_listing")
    connection.unregister("quality_listing")
    return connection.sql(_statement("listing_quality")).to_arrow_table()


def feature_patterns(
    connection: duckdb.DuckDBPyConnection, listing: pa.Table, listing_text: pa.Table
) -> pa.Table:
    """The ``feature_pattern`` rows: every feature wording of Silver's ``listing_text``.

    The given tables replace the temporary tables ``silver_listing`` and ``silver_listing_text``:
    call this after every other statement of the session that reads them. ``listing`` needs the
    key and every attribute column a registry target names; ``listing_text`` needs the key and
    ``features``.
    """
    _prepare(connection)
    _replace_silver(connection, {"listing": listing, "listing_text": listing_text})
    return connection.sql(_statement("feature_pattern")).to_arrow_table()


def listing_feature_labels(
    connection: duckdb.DuckDBPyConnection, listing_text: pa.Table
) -> pa.Table:
    """The ``listing_feature_label`` rows: one per row of Silver's ``listing_text``.

    The given table, which needs the key and ``features``, replaces the temporary table
    ``silver_listing_text``: call this after every other statement of the session that reads it.
    """
    _prepare(connection)
    _replace_silver(connection, {"listing_text": listing_text})
    return connection.sql(_statement("listing_feature_label")).to_arrow_table()


def listing_locations(
    connection: duckdb.DuckDBPyConnection, listing: pa.Table, sections: pa.Table
) -> pa.Table:
    """The ``listing_location`` rows: one per listing with a detail record.

    ``listing`` needs ``LOCATION_LISTING_COLUMNS``; ``sections`` is one reference vintage with its
    six columns. Both are registered under names of this rule and removed again, so no
    ``silver_<table>`` of the session is replaced. The session needs DuckDB's spatial extension
    installed.
    """
    _prepare(connection)
    connection.execute("LOAD spatial")
    connection.register("location_listing", listing)
    connection.register("location_sections", sections)
    try:
        return connection.sql(_statement(LOCATION_TABLE)).to_arrow_table()
    finally:
        connection.unregister("location_listing")
        connection.unregister("location_sections")


def listing_text_labels(
    connection: duckdb.DuckDBPyConnection,
    listing: pa.Table,
    listing_text: pa.Table,
    answers: pa.Table,
) -> pa.Table:
    """The ``listing_text_label`` rows: one per row of Silver's ``listing_text``.

    ``listing`` needs ``TEXT_LABEL_LISTING_COLUMNS``, ``listing_text`` ``TEXT_LABEL_TEXT_COLUMNS``
    and ``answers`` the stored answers' text_sha256, label_version, model_id, renovation_state,
    is_auction and labeled_at. All three are registered under names of this rule and removed
    again, so no ``silver_<table>`` of the session is replaced.
    """
    _prepare(connection)
    connection.execute("SET VARIABLE label_version = ?", [LABEL_VERSION])
    inputs = {
        "text_label_listing": listing.select(list(TEXT_LABEL_LISTING_COLUMNS)),
        "text_label_text": listing_text.select(list(TEXT_LABEL_TEXT_COLUMNS)),
        "text_label_answer": answers.select(
            [
                "text_sha256",
                "label_version",
                "model_id",
                "renovation_state",
                "is_auction",
                "labeled_at",
            ]
        ),
    }
    for name, table in inputs.items():
        connection.register(name, table)
    try:
        return connection.sql(_statement(TEXT_LABEL_TABLE)).to_arrow_table()
    finally:
        for name in inputs:
            connection.unregister(name)


def _replace_silver(connection: duckdb.DuckDBPyConnection, tables: Mapping[str, pa.Table]) -> None:
    for name, rows in tables.items():
        connection.register(f"given_{name}", rows)
        connection.execute(f"CREATE OR REPLACE TEMP TABLE silver_{name} AS FROM given_{name}")
        connection.unregister(f"given_{name}")


def _prepare(connection: duckdb.DuckDBPyConnection) -> None:
    # DuckDB exports instants in the session zone and Iceberg accepts only UTC.
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute(f"SET VARIABLE transformation_version = '{TRANSFORMATION_VERSION}'")
    connection.execute(f"SET VARIABLE redaction_version = '{REDACTION_VERSION}'")
    for name, value in QUALITY_THRESHOLDS.items():
        connection.execute(f"SET VARIABLE {name} = {value}::BIGINT")
    connection.execute(_statement("redact"))
    register_feature_registry(connection)
    without_column = ", ".join(f"'{target}'" for target in (*LABEL_TARGETS, UNREAD))
    connection.execute(f"SET VARIABLE feature_targets_without_column = [{without_column}]")


def _statement(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


METRICS_NAMESPACE = "vlc-housing-silver"


def s3_commits(s3_client, location: str, exclude: Collection[str]) -> list[LandingCommit]:
    """List an ``s3://`` landing v3's commits and read only those whose run is not in ``exclude``.

    Listing is one paginated call over a few thousand keys a year; reading every commit on every
    run would cost one GET per run ever landed, so ingested runs are known by their key alone.
    """
    bucket, _, prefix = location.removeprefix("s3://").partition("/")
    prefix = f"{prefix.strip('/')}/_commits/"
    commits = []
    for page in s3_client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            run_id = item["Key"].rsplit("/", 1)[1].removesuffix(".json")
            if run_id in exclude:
                continue
            body = s3_client.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            commits.append(parse_commit(json.loads(body)))
    return commits


def main(argv: Sequence[str] | None = None, *, s3_client=None, cloudwatch_client=None) -> int:
    """Run Silver once; exit 0 on success, 2 on usage.

    Exit 1 when landing, a table schema, the census-section vintage or a stored answer is
    refused.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.silver.rebuild", description="Maintain Silver from landing v3."
    )
    parser.add_argument(
        "--landing", required=True, help="landing v3: a directory or s3://bucket/prefix"
    )
    parser.add_argument(
        "--catalog",
        required=True,
        help="PyIceberg catalog name, configured in ~/.pyiceberg.yaml or PYICEBERG_CATALOG__* "
        "environment variables",
    )
    parser.add_argument("--mode", choices=("incremental", "backfill"), default="incremental")
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="publish PendingRuns and, after a run, UnknownFeaturePatterns to CloudWatch",
    )
    parser.add_argument(
        "--census-sections",
        help="one census-section vintage, a directory or s3://bucket/prefix (ADR 0004); "
        "a run given it also writes listing_location",
    )
    parser.add_argument(
        "--text-labels",
        help="the text labeller's stored answers, a directory or s3://bucket/prefix (ADR 0005); "
        "a run given it also writes listing_text_label",
    )
    arguments = parser.parse_args(argv)
    on_s3 = arguments.landing.startswith("s3://")
    if not on_s3 and not Path(arguments.landing).is_dir():
        parser.error(f"--landing {arguments.landing} is not a directory")

    catalog = load_catalog(arguments.catalog)
    exclude = set() if arguments.mode == "backfill" else set(ingested_runs(catalog, NAMESPACE))
    if on_s3:
        if s3_client is None:
            import boto3

            s3_client = boto3.client("s3")
        todo = s3_commits(s3_client, arguments.landing, exclude)
    else:
        todo = [c for c in local_commits(Path(arguments.landing)) if c.run_id not in exclude]

    remaining = len(todo)
    unknown_feature_patterns = None
    try:
        if not todo:
            print("nothing pending")
        else:
            with duckdb.connect() as connection:
                if on_s3 or any(
                    (location or "").startswith("s3://")
                    for location in (arguments.census_sections, arguments.text_labels)
                ):
                    _read_s3_through_duckdb(connection)
                result = process_landing(
                    connection,
                    catalog,
                    landing=arguments.landing.rstrip("/"),
                    mode=arguments.mode,
                    list_commits=lambda: todo,
                    now=lambda: datetime.now(UTC),
                    census_sections=arguments.census_sections,
                    text_labels=arguments.text_labels,
                )
            remaining = 0
            unknown_feature_patterns = result.unknown_feature_patterns
            _report(result)
    except (
        LandingContractError,
        SilverSchemaError,
        ReferenceDataError,
        StoredAnswerError,
    ) as error:
        print(f"Silver run refused: {error}", file=sys.stderr)
        return 1
    finally:
        if arguments.metrics:
            _publish_metrics(cloudwatch_client, remaining, unknown_feature_patterns)
    return 0


def _read_s3_through_duckdb(connection: duckdb.DuckDBPyConnection) -> None:
    # The image pre-installs both extensions, so LOAD needs no network; the secret resolves the
    # task role through the AWS credential chain.
    connection.execute("LOAD httpfs")
    connection.execute("LOAD aws")
    connection.execute("CREATE OR REPLACE SECRET silver_s3 (TYPE s3, PROVIDER credential_chain)")


def _report(result: ProcessResult) -> None:
    print(f"pending: {result.pending}")
    print(f"processed: {', '.join(result.processed)}")
    for name in DAY_TABLES:
        print(f"{name}: {len(result.days[name])} days, snapshot {result.snapshot_ids.get(name)}")
    print(f"affected listings: {result.affected}")
    for name in (
        "listing",
        "listing_text",
        "listing_quality",
        "listing_event",
        PROPERTY_GROUP_TABLE,
        "listing_price",
        "feature_pattern",
        "listing_feature_label",
    ):
        print(f"{name}: snapshot {result.snapshot_ids.get(name)}")
    if LOCATION_TABLE in result.snapshot_ids:
        print(f"{LOCATION_TABLE}: snapshot {result.snapshot_ids[LOCATION_TABLE]}")
    if TEXT_LABEL_TABLE in result.snapshot_ids:
        print(f"{TEXT_LABEL_TABLE}: snapshot {result.snapshot_ids[TEXT_LABEL_TABLE]}")
    print(f"unknown feature patterns: {result.unknown_feature_patterns}")


def _publish_metrics(
    cloudwatch_client, remaining: int, unknown_feature_patterns: int | None
) -> None:
    """Publish PendingRuns, and UnknownFeaturePatterns when a run computed it.

    A run with nothing pending computed nothing, so it leaves the last count standing instead of
    publishing a zero it did not measure.
    """
    if cloudwatch_client is None:
        import boto3

        cloudwatch_client = boto3.client("cloudwatch")
    metrics = {"PendingRuns": remaining}
    if unknown_feature_patterns is not None:
        metrics["UnknownFeaturePatterns"] = unknown_feature_patterns
    cloudwatch_client.put_metric_data(
        Namespace=METRICS_NAMESPACE,
        MetricData=[
            {"MetricName": name, "Value": value, "Unit": "Count"} for name, value in metrics.items()
        ],
    )


if __name__ == "__main__":
    sys.exit(main())
