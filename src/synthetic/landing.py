"""Write landing tables that conform to the contract, from hand-written or generated rows."""

from collections.abc import Mapping, Sequence
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
