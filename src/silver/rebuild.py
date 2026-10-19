"""Rebuild Silver from landing v1: DuckDB SQL computes the tables, Iceberg stores them.

Run from the repository root::

    python -m src.silver.rebuild --landing <local landing/v1 directory> --catalog <name>
"""

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.expressions import In

from src.contracts import landing_v1_sha256, landing_v3_sha256
from src.silver.affected import ListingKey, affected_listings
from src.silver.commits import LandingCommit, days_of, parse_commit, pending, runs_for
from src.silver.ingest import ingested_runs, record_ingested
from src.silver.landing import LandingContractError, register_landing, register_landing_v3
from src.silver.sink import SilverSchemaError, replace_days, upsert_rows, write_silver_tables

SQL_DIR = Path(__file__).resolve().parent / "sql"

NAMESPACE = "vlc_housing_silver"
TRANSFORMATION_VERSION = "silver-v1"
REDACTION_VERSION = "r1"

# In dependency order: a statement may read the silver_<table> of any table listed before it.
SILVER_TABLES = ("walk", "observation", "listing_detail", "listing", "listing_text")

# Intermediate views shared by several Silver statements, created before them.
SHARED_VIEWS = ("chosen_detail",)

# Listings are upserted on this key in listing and listing_text.
LISTING_KEY = ["source", "source_listing_id"]


@dataclass(frozen=True)
class RebuildResult:
    """What one rebuild committed: rows and the snapshot a training dataset can pin, per table."""

    row_counts: dict[str, int]
    snapshot_ids: dict[str, int]


def rebuild(
    connection: duckdb.DuckDBPyConnection,
    catalog: Catalog,
    *,
    landing: Path,
    now: Callable[[], datetime],
) -> RebuildResult:
    """Rebuild every Silver table from the landing directory and commit it to ``catalog``.

    Every table is computed before the first commit, so a landing that fails the contract writes
    nothing. The commits themselves are per table: a failure between them leaves tables at
    different rebuilds, visible as differing ``rebuild_started_at`` and repaired by a rerun.
    """
    register_landing(connection, landing)
    tables = build_tables(connection)
    properties = {
        "transformation_version": TRANSFORMATION_VERSION,
        "redaction_version": REDACTION_VERSION,
        "landing_contract_sha256": landing_v1_sha256(),
        "rebuild_started_at": now().astimezone(UTC).isoformat(),
    }
    snapshot_ids = write_silver_tables(catalog, NAMESPACE, tables, properties)
    return RebuildResult(
        row_counts={name: table.num_rows for name, table in tables.items()},
        snapshot_ids=snapshot_ids,
    )


# The tables replaced per day; listing and listing_text are recomputed per affected listing.
DAY_TABLES = ("walk", "observation", "listing_detail")


@dataclass(frozen=True)
class ProcessResult:
    """What one incremental or backfill run did."""

    pending: int
    processed: tuple[str, ...]
    days: dict[str, tuple[date, ...]]
    affected: int
    snapshot_ids: dict[str, int | None]


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
) -> ProcessResult:
    """Process the landing runs Silver has not ingested yet; ``backfill`` treats every run as new.

    Pending runs are landing's commits minus the ingest log. The days they touch are rebuilt from
    every run that touches them, the listings those days held before or hold now are recomputed
    from Silver's history, and the ingest log is written last: a run that stops before it leaves
    its runs pending, and repeating them gives the same rows.
    """
    if mode not in ("incremental", "backfill"):
        raise ValueError(f"unknown mode {mode!r}")
    commits = list_commits()
    ingested = {} if mode == "backfill" else ingested_runs(catalog, NAMESPACE)
    todo = pending(commits, set(ingested))
    if not todo:
        return ProcessResult(0, (), {}, 0, {})

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
        snapshot_ids[name] = replace_days(catalog, NAMESPACE, name, rows, named, properties)

    affected = set().union(*(affected_listings(p, n) for p, n in zip(previous, new, strict=True)))
    snapshot_ids |= update_listings(
        connection,
        catalog,
        landing=landing,
        affected=affected,
        run_started=run_started,
        properties=properties,
    )
    record_ingested(catalog, NAMESPACE, todo, now(), TRANSFORMATION_VERSION)
    return ProcessResult(
        pending=len(todo),
        processed=tuple(commit.run_id for commit in todo),
        days={name: tuple(sorted(days[name])) for name in DAY_TABLES},
        affected=len(affected),
        snapshot_ids=snapshot_ids,
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

    snapshot_ids: dict[str, int | None] = {}
    for name, rows in (("listing", listing), ("listing_text", listing_text)):
        present = set(
            zip(rows["source"].to_pylist(), rows["source_listing_id"].to_pylist(), strict=True)
        )
        snapshot_ids[name] = upsert_rows(
            catalog, NAMESPACE, name, rows, LISTING_KEY, affected - present, properties
        )
    return snapshot_ids


def _prepare(connection: duckdb.DuckDBPyConnection) -> None:
    # DuckDB exports instants in the session zone and Iceberg accepts only UTC.
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute(f"SET VARIABLE transformation_version = '{TRANSFORMATION_VERSION}'")
    connection.execute(f"SET VARIABLE redaction_version = '{REDACTION_VERSION}'")
    connection.execute(_statement("redact"))


def _statement(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Rebuild Silver once; exit 0 on success, 1 when landing or a table schema is refused."""
    parser = argparse.ArgumentParser(
        prog="python -m src.silver.rebuild", description="Rebuild Silver from landing v1."
    )
    parser.add_argument(
        "--landing", type=Path, required=True, help="local directory holding landing/v1 tables"
    )
    parser.add_argument(
        "--catalog",
        required=True,
        help="PyIceberg catalog name, configured in ~/.pyiceberg.yaml or PYICEBERG_CATALOG__* "
        "environment variables",
    )
    arguments = parser.parse_args(argv)
    if not arguments.landing.is_dir():
        parser.error(f"--landing {arguments.landing} is not a directory")

    catalog = load_catalog(arguments.catalog)
    with duckdb.connect() as connection:
        try:
            result = rebuild(
                connection, catalog, landing=arguments.landing, now=lambda: datetime.now(UTC)
            )
        except (LandingContractError, SilverSchemaError) as error:
            print(f"Silver rebuild refused: {error}", file=sys.stderr)
            return 1
    for table in SILVER_TABLES:
        print(f"{table}: {result.row_counts[table]} rows, snapshot {result.snapshot_ids[table]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
