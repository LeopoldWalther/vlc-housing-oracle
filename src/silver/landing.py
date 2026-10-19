"""Register landing as DuckDB views, after checking every file against the contract.

Landing v3 is read per collection run: the views cover exactly the named runs' files, each file
is checked against the v3 columns and, for a run not ingested yet, against its commit's row
count; keys, nulls and the detail/text pairing are checked within each run, because across runs
nothing is deduplicated by landing.
"""

from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import Path

import duckdb

from src.contracts import landing_v1_columns, landing_v3_columns

# Columns Silver's keys and orderings depend on. A null here would change which row a rebuild
# chooses (a null is_empty_card sorts after an empty card), so it stops the rebuild instead.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "walk": ("source", "area", "observed_date"),
    "observation": (
        "source",
        "area",
        "source_listing_id",
        "observed_at",
        "observed_date",
        "is_empty_card",
    ),
    "listing_detail": ("source", "area", "source_listing_id", "observed_at", "observed_date"),
    "listing_text": ("source", "area", "source_listing_id", "observed_at", "observed_date"),
}

# Keys unique within one area. The total orders Silver dedupes by end on these columns, so a
# repeated key would make the choice between two rows undefined.
UNIQUE_KEYS: dict[str, tuple[str, ...]] = {
    "observation": ("source", "area", "source_listing_id", "observed_date"),
    "listing_detail": ("source", "area", "source_listing_id", "observed_at"),
    "listing_text": ("source", "area", "source_listing_id", "observed_at"),
}

DETAIL_TEXT_KEY = UNIQUE_KEYS["listing_detail"]


class LandingContractError(Exception):
    """Landing does not conform to the contract; no Silver table may be built from it."""


def register_landing(connection: duckdb.DuckDBPyConnection, directory: Path) -> None:
    """Create ``landing_<table>`` views over ``directory`` once every check passes.

    Messages name tables, columns and counts, never row values: landing is private data.
    """
    for table, columns in landing_v1_columns().items():
        path = directory / f"{table}.parquet"
        if not path.is_file():
            raise LandingContractError(f"landing table {table} is missing: no {path.name}")
        _check_columns(connection, table, path, columns)
        view = f"landing_{table}"
        literal = str(path).replace("'", "''")
        # Transitional, until the v1 path is removed: v1 reads as one pseudo-run, so the
        # statements written for landing v3 run over it unchanged.
        connection.execute(
            f"CREATE OR REPLACE VIEW {view} AS "
            f"SELECT source, 'v1' AS run_id, * EXCLUDE (source) FROM read_parquet('{literal}')"
        )
        _check_not_null(connection, table, view)
        if table in UNIQUE_KEYS:
            _check_unique(connection, table, view, UNIQUE_KEYS[table])
    _check_paired(connection, "listing_detail", "listing_text")
    _check_paired(connection, "listing_text", "listing_detail")
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW landing_runs AS "
        "SELECT 'v1' AS run_id, NULL::TIMESTAMPTZ AS run_started_at"
    )


def _check_columns(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    path: Path | str,
    expected: list[tuple[str, str]],
    *,
    hive_partitioning: bool = True,
) -> None:
    literal = str(path).replace("'", "''")
    options = "" if hive_partitioning else ", hive_partitioning = false"
    described = connection.sql(
        f"DESCRIBE SELECT * FROM read_parquet('{literal}'{options})"
    ).fetchall()
    actual = [(name, column_type) for name, column_type, *_ in described]
    if actual == expected:
        return
    for position, (found, wanted) in enumerate(zip(actual, expected, strict=False), start=1):
        if found != wanted:
            raise LandingContractError(
                f"landing table {table}: column {position} is {found[0]} {found[1]}, "
                f"the contract expects {wanted[0]} {wanted[1]}"
            )
    raise LandingContractError(
        f"landing table {table} has {len(actual)} columns, the contract expects {len(expected)}"
    )


def _check_not_null(
    connection: duckdb.DuckDBPyConnection, table: str, view: str, extra: tuple[str, ...] = ()
) -> None:
    columns = (*extra, *REQUIRED_COLUMNS[table])
    counts = connection.sql(
        "SELECT "
        + ", ".join(f"count(*) FILTER (WHERE {c} IS NULL)" for c in columns)
        + f" FROM {view}"
    ).fetchone()
    for column, count in zip(columns, counts, strict=True):
        if count:
            raise LandingContractError(f"landing table {table}: {count} rows have a null {column}")


def _check_unique(
    connection: duckdb.DuckDBPyConnection, table: str, view: str, key: tuple[str, ...]
) -> None:
    key_list = ", ".join(key)
    repeated = connection.sql(
        f"SELECT count(*) FROM (SELECT {key_list} FROM {view} GROUP BY ALL HAVING count(*) > 1)"
    ).fetchone()[0]
    if repeated:
        raise LandingContractError(
            f"landing table {table}: {repeated} keys ({key_list}) occur more than once"
        )


def _check_paired(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    partner: str,
    runs: Collection[str] | None = None,
) -> None:
    # With runs given (landing v3), the pairing holds within each run, so only runs read for both
    # tables are compared; listing_text may deliberately be read over other runs.
    if runs is not None and not runs:
        return
    key = DETAIL_TEXT_KEY if runs is None else ("run_id", *DETAIL_TEXT_KEY)
    key_list = ", ".join(key)
    within = (
        ""
        if runs is None
        else " WHERE run_id IN (" + ", ".join(_literal(r) for r in sorted(runs)) + ")"
    )
    unpaired = connection.sql(
        f"SELECT count(*) FROM (SELECT {key_list} FROM landing_{table}{within} "
        f"EXCEPT SELECT {key_list} FROM landing_{partner}{within})"
    ).fetchone()[0]
    if unpaired:
        raise LandingContractError(
            f"landing table {table}: {unpaired} rows have no {partner} row on ({key_list})"
        )


def register_landing_v3(
    connection: duckdb.DuckDBPyConnection,
    location: str,
    runs_by_table: Mapping[str, Collection[str]],
    run_started: Mapping[str, datetime],
    expected_counts: Mapping[str, Mapping[str, int]],
) -> None:
    """Create ``landing_<table>`` views over the named runs, and ``landing_runs``, once checked.

    ``location`` is a local directory or an ``s3://`` prefix holding landing v3; reading S3 needs
    ``httpfs`` set up on the connection by the caller. ``run_started`` gives every named run's
    start, which addresses its ``run_date`` partition. ``expected_counts`` holds the commit's
    ``row_counts`` for runs not ingested yet; files of ingested runs are immutable and were counted
    when they were first read. Messages name tables, runs and counts, never row values.
    """
    location = location.rstrip("/")
    contract = landing_v3_columns()
    for table, columns in contract.items():
        runs = sorted(runs_by_table.get(table, ()))
        files = [_v3_file(location, table, run_id, run_started) for run_id in runs]
        for run_id, path in zip(runs, files, strict=True):
            _check_v3_file(connection, table, run_id, path, columns, expected_counts.get(run_id))
        view = f"landing_{table}"
        if files:
            listed = ", ".join(_literal(path) for path in files)
            source = f"read_parquet([{listed}], hive_partitioning = false)"
            connection.execute(f"CREATE OR REPLACE TEMP VIEW {view} AS SELECT * FROM {source}")
        else:
            typed = ", ".join(f'CAST(NULL AS {kind}) AS "{name}"' for name, kind in columns)
            connection.execute(f"CREATE OR REPLACE TEMP VIEW {view} AS SELECT {typed} WHERE false")
        _check_not_null(connection, table, view, extra=("run_id",))
        if table in UNIQUE_KEYS:
            _check_unique(connection, table, view, ("run_id", *UNIQUE_KEYS[table]))
    both = set(runs_by_table.get("listing_detail", ())) & set(runs_by_table.get("listing_text", ()))
    _check_paired(connection, "listing_detail", "listing_text", runs=both)
    _check_paired(connection, "listing_text", "listing_detail", runs=both)
    started = ", ".join(
        f"({_literal(run_id)}, TIMESTAMPTZ {_literal(moment.isoformat())})"
        for run_id, moment in sorted(run_started.items())
    )
    connection.execute(
        "CREATE OR REPLACE TEMP VIEW landing_runs AS "
        + (
            f"SELECT * FROM (VALUES {started}) runs(run_id, run_started_at)"
            if started
            else "SELECT NULL::VARCHAR AS run_id, NULL::TIMESTAMPTZ AS run_started_at WHERE false"
        )
    )


def _v3_file(location: str, table: str, run_id: str, run_started: Mapping[str, datetime]) -> str:
    if run_id not in run_started:
        raise LandingContractError(f"landing table {table}: run {run_id} has no known start")
    run_date = run_started[run_id].date().isoformat()
    return f"{location}/{table}/run_date={run_date}/run={run_id}/part-0.parquet"


def _check_v3_file(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    run_id: str,
    path: str,
    expected: list[tuple[str, str]],
    counts: Mapping[str, int] | None,
) -> None:
    if "://" not in path and not Path(path).is_file():
        raise LandingContractError(f"landing table {table}: run {run_id} has no file")
    try:
        _check_columns(connection, table, path, expected, hive_partitioning=False)
        found = connection.sql(
            f"SELECT count(*) FROM read_parquet({_literal(path)}, hive_partitioning = false)"
        ).fetchone()[0]
    except duckdb.IOException as error:
        raise LandingContractError(f"landing table {table}: run {run_id} cannot be read") from error
    if counts is not None and counts.get(table) != found:
        raise LandingContractError(
            f"landing table {table}: run {run_id} commit says {counts.get(table)} rows, "
            f"its file holds {found}"
        )


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
