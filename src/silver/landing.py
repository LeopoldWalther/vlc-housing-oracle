"""Register landing v1 as DuckDB views, after checking every file against the contract."""

from pathlib import Path

import duckdb

from src.contracts import landing_v1_columns

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
        connection.execute(
            f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet('{literal}')"
        )
        _check_not_null(connection, table, view)
        if table in UNIQUE_KEYS:
            _check_unique(connection, table, view, UNIQUE_KEYS[table])
    _check_paired(connection, "listing_detail", "listing_text")
    _check_paired(connection, "listing_text", "listing_detail")


def _check_columns(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    path: Path,
    expected: list[tuple[str, str]],
) -> None:
    literal = str(path).replace("'", "''")
    described = connection.sql(f"DESCRIBE SELECT * FROM read_parquet('{literal}')").fetchall()
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


def _check_not_null(connection: duckdb.DuckDBPyConnection, table: str, view: str) -> None:
    columns = REQUIRED_COLUMNS[table]
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


def _check_paired(connection: duckdb.DuckDBPyConnection, table: str, partner: str) -> None:
    key_list = ", ".join(DETAIL_TEXT_KEY)
    unpaired = connection.sql(
        f"SELECT count(*) FROM (SELECT {key_list} FROM landing_{table} "
        f"EXCEPT SELECT {key_list} FROM landing_{partner})"
    ).fetchone()[0]
    if unpaired:
        raise LandingContractError(
            f"landing table {table}: {unpaired} rows have no {partner} row on ({key_list})"
        )
