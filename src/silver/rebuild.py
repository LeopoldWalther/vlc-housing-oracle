"""Rebuild Silver from landing v1: DuckDB SQL computes the tables, Iceberg stores them."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
from pyiceberg.catalog import Catalog

from src.contracts import landing_v1_sha256
from src.silver.landing import register_landing
from src.silver.sink import write_silver_tables

SQL_DIR = Path(__file__).resolve().parent / "sql"

NAMESPACE = "vlc_housing_silver"
TRANSFORMATION_VERSION = "silver-v1"
REDACTION_VERSION = "r1"

# In dependency order: a statement may read the silver_<table> of any table listed before it.
SILVER_TABLES = ("walk", "observation", "listing", "listing_text")

# Intermediate views shared by several Silver statements, created before them.
SHARED_VIEWS = ("chosen_detail",)


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


def build_tables(connection: duckdb.DuckDBPyConnection) -> dict[str, pa.Table]:
    """Compute every Silver table from the registered ``landing_<table>`` views.

    Each result is also kept as the temporary table ``silver_<table>`` for later statements. The
    session runs in UTC because DuckDB exports instants in the session zone and Iceberg accepts
    only UTC.
    """
    connection.execute("SET TimeZone = 'UTC'")
    connection.execute(f"SET VARIABLE transformation_version = '{TRANSFORMATION_VERSION}'")
    connection.execute(f"SET VARIABLE redaction_version = '{REDACTION_VERSION}'")
    connection.execute(_statement("redact"))
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
