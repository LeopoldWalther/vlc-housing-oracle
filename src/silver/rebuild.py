"""Rebuild Silver from landing v1: DuckDB SQL computes the tables, Iceberg stores them."""

from pathlib import Path

import duckdb
import pyarrow as pa

SQL_DIR = Path(__file__).resolve().parent / "sql"

TRANSFORMATION_VERSION = "silver-v1"

# In dependency order: a statement may read the silver_<table> of any table listed before it.
SILVER_TABLES = ("walk", "observation", "listing")

# Intermediate views shared by several Silver statements, created before them.
SHARED_VIEWS = ("chosen_detail",)


def build_tables(connection: duckdb.DuckDBPyConnection) -> dict[str, pa.Table]:
    """Compute every Silver table from the registered ``landing_<table>`` views.

    Each result is also kept as the temporary table ``silver_<table>`` for later statements. The
    session runs in UTC because DuckDB exports instants in the session zone and Iceberg accepts
    only UTC.
    """
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute(f"SET VARIABLE transformation_version = '{TRANSFORMATION_VERSION}'")
    for name in SHARED_VIEWS:
        connection.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS {_statement(name)}")
    tables: dict[str, pa.Table] = {}
    for name in SILVER_TABLES:
        statement = _statement(name)
        connection.execute(f"CREATE OR REPLACE TEMP TABLE silver_{name} AS {statement}")
        tables[name] = connection.table(f"silver_{name}").to_arrow_table()
    return tables


def _statement(name: str) -> str:
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
