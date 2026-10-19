"""Landing v3 commits, the runs Silver has not processed yet, and the runs that touch a day.

Pending work is a set difference over immutable run ids, never a clock or a watermark
(ADR 0003): a commit is pending exactly when its run id is not in Silver's ingest log.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from src.contracts import landing_v3_columns, landing_v3_sha256
from src.silver.landing import LandingContractError

COMMIT_FIELDS = (
    "contract",
    "version",
    "schema_sha256",
    "run_id",
    "run_started_at",
    "outcome",
    "is_replay",
    "row_counts",
    "observed_dates",
    "written_at",
)

ObservedDates = Mapping[str, frozenset[date]]


@dataclass(frozen=True)
class LandingCommit:
    """One collection run as landing v3 announced it."""

    run_id: str
    run_started_at: datetime
    outcome: str
    is_replay: bool
    row_counts: Mapping[str, int]
    observed_dates: ObservedDates
    written_at: datetime


def parse_commit(raw: Mapping[str, object]) -> LandingCommit:
    """Parse one commit's JSON object, refusing anything that is not a landing v3 commit."""
    run_id = str(raw.get("run_id", "<no run_id>"))
    missing = [field for field in COMMIT_FIELDS if field not in raw]
    if missing:
        raise LandingContractError(f"landing commit {run_id}: missing {', '.join(missing)}")
    if (raw["contract"], raw["version"]) != ("landing", "v3"):
        raise LandingContractError(
            f"landing commit {run_id}: {raw['contract']} {raw['version']} is not landing v3"
        )
    if raw["schema_sha256"] != landing_v3_sha256():
        raise LandingContractError(f"landing commit {run_id}: schema digest is not landing v3's")
    tables = set(landing_v3_columns())
    for field in ("row_counts", "observed_dates"):
        absent = sorted(tables - set(raw[field]))
        if absent:
            raise LandingContractError(
                f"landing commit {run_id}: {field} lacks {', '.join(absent)}"
            )
    return LandingCommit(
        run_id=run_id,
        run_started_at=datetime.fromisoformat(str(raw["run_started_at"])),
        outcome=str(raw["outcome"]),
        is_replay=bool(raw["is_replay"]),
        row_counts={table: int(raw["row_counts"][table]) for table in tables},
        observed_dates={
            table: frozenset(date.fromisoformat(day) for day in raw["observed_dates"][table])
            for table in tables
        },
        written_at=datetime.fromisoformat(str(raw["written_at"])),
    )


def pending(commits: Iterable[LandingCommit], ingested: Collection[str]) -> list[LandingCommit]:
    """Return the commits whose run is not ingested, oldest run first.

    Order is by the run's start, then its id: an ``import-<date>`` id sorts after every timestamp
    id as a string, so the id alone would put imports last.
    """
    return sorted(
        (commit for commit in commits if commit.run_id not in ingested),
        key=lambda commit: (commit.run_started_at, commit.run_id),
    )


def days_of(commits: Iterable[LandingCommit]) -> dict[str, set[date]]:
    """Union, per table, of the days the commits' rows touch."""
    days: dict[str, set[date]] = {table: set() for table in landing_v3_columns()}
    for commit in commits:
        for table, observed in commit.observed_dates.items():
            days[table] |= observed
    return days


def runs_for(
    days: Mapping[str, set[date]], known: Mapping[str, Mapping[str, Collection[date]]]
) -> dict[str, set[str]]:
    """Every known run, pending or ingested, whose rows touch a named day, per table.

    A day-partitioned table is rewritten from all of landing because several runs can touch one
    day, so the run set for a day is wider than the pending runs alone.
    """
    return {
        table: {
            run_id for run_id, observed in known.items() if set(observed.get(table, ())) & named
        }
        for table, named in days.items()
    }
