"""Silver's ingest log: which landing runs it has processed, written last in every run.

The log is the checkpoint of ADR 0003: pending work is landing's commits minus this table. It is
upserted by ``run_id``, so repeating a run never duplicates a row. ``outcome`` and ``is_replay``
are copied from the commit for audit only and are never read by a rule: markers imported before
2026-10-14 carry ``is_replay = false`` for replays, while the rows' own ``is_replay`` is right.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

import pyarrow as pa
from pyiceberg.catalog import Catalog

from src.contracts import landing_v3_columns
from src.silver.commits import LandingCommit
from src.silver.sink import upsert_rows

INGEST_TABLE = "landing_ingest"


@dataclass(frozen=True)
class IngestedRun:
    """A landing run Silver has processed, with what a later run needs to know about it."""

    run_id: str
    run_started_at: datetime
    observed_dates: Mapping[str, frozenset[date]]


def _schema() -> pa.Schema:
    instant = pa.timestamp("us", tz="UTC")
    return pa.schema(
        [
            ("run_id", pa.string()),
            ("run_started_at", instant),
            ("outcome", pa.string()),
            ("is_replay", pa.bool_()),
            *((f"observed_dates_{table}", pa.list_(pa.date32())) for table in landing_v3_columns()),
            ("commit_written_at", instant),
            ("ingested_at", instant),
            ("transformation_version", pa.string()),
        ]
    )


def ingested_runs(catalog: Catalog, namespace: str) -> dict[str, IngestedRun]:
    """Every run in the ingest log, by run id; empty when the log does not exist yet."""
    identifier = f"{namespace}.{INGEST_TABLE}"
    if not catalog.table_exists(identifier):
        return {}
    tables = list(landing_v3_columns())
    return {
        row["run_id"]: IngestedRun(
            run_id=row["run_id"],
            run_started_at=row["run_started_at"],
            observed_dates={
                table: frozenset(row[f"observed_dates_{table}"] or ()) for table in tables
            },
        )
        for row in catalog.load_table(identifier).scan().to_arrow().to_pylist()
    }


def record_ingested(
    catalog: Catalog,
    namespace: str,
    commits: Iterable[LandingCommit],
    now: datetime,
    transformation_version: str,
) -> None:
    """Upsert one row per commit; call it after every other table of the run is written."""
    tables = list(landing_v3_columns())
    rows = [
        {
            "run_id": commit.run_id,
            "run_started_at": commit.run_started_at,
            "outcome": commit.outcome,
            "is_replay": commit.is_replay,
            **{f"observed_dates_{table}": sorted(commit.observed_dates[table]) for table in tables},
            "commit_written_at": commit.written_at,
            "ingested_at": now,
            "transformation_version": transformation_version,
        }
        for commit in commits
    ]
    if rows:
        upsert_rows(
            catalog,
            namespace,
            INGEST_TABLE,
            pa.Table.from_pylist(rows, schema=_schema()),
            ["run_id"],
            (),
            {"transformation_version": transformation_version},
        )
