"""Property groups: listings that are certainly the same property, under rule pg1."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow.fs import FileSystem
from pyiceberg.catalog import Catalog

from src.silver.rebuild import NAMESPACE

SQL_DIR = Path(__file__).resolve().parent / "sql"
DATASET = "listing_property_group"

# The columns of Silver's listing that the rule and the rows read.
LISTING_COLUMNS = (
    "source",
    "source_listing_id",
    "detail_observed_at",
    "operation",
    "municipality",
    "rooms",
    "bathrooms",
    "sqm_built",
    "price_eur",
    "latitude",
    "longitude",
    "floor",
    "condition",
)

ListingKey = tuple[str, str]

PROPERTY_GROUP_VERSION = "pg1"
PRICE_GAP_MAX_PERCENT = 5
DISTANCE_MAX_METERS = 350

# The dataset listing_property_group, one row per listing with a detail record.
PROPERTY_GROUP_SCHEMA = pa.schema(
    [
        ("source", pa.string()),
        ("source_listing_id", pa.string()),
        ("property_group_id", pa.string()),
        ("group_size", pa.int32()),
        ("group_is_complete", pa.bool_()),
        ("property_group_version", pa.string()),
        ("silver_listing_snapshot_id", pa.int64()),
        ("built_at", pa.timestamp("us", tz="UTC")),
    ]
)


def duplicate_pairs(connection: duckdb.DuckDBPyConnection, listing: pa.Table) -> pa.Table:
    """Every pair of listings that rule pg1 calls the same property, each once, smaller key first.

    Columns: source_a, source_listing_id_a, source_b, source_listing_id_b.
    """
    connection.execute(f"SET VARIABLE price_gap_max_percent = {PRICE_GAP_MAX_PERCENT}::BIGINT")
    connection.execute(f"SET VARIABLE distance_max_meters = {DISTANCE_MAX_METERS}")
    connection.register("gold_listing", listing.select(LISTING_COLUMNS))
    return connection.execute((SQL_DIR / "pairs.sql").read_text(encoding="utf-8")).to_arrow_table()


def _pair_keys(pairs: pa.Table) -> list[tuple[ListingKey, ListingKey]]:
    return [
        (
            (row["source_a"], row["source_listing_id_a"]),
            (row["source_b"], row["source_listing_id_b"]),
        )
        for row in pairs.to_pylist()
    ]


def group_of(pairs: pa.Table) -> dict[ListingKey, ListingKey]:
    """Map every listing in a pair to the smallest key of its connected component.

    The pair rule is not transitive, so groups are the connected components of the pair graph:
    a partition of the listings needs them. Keys compare as (source, source_listing_id).
    """
    parent: dict[ListingKey, ListingKey] = {}

    def find(key: ListingKey) -> ListingKey:
        root = key
        while parent[root] != root:
            root = parent[root]
        while parent[key] != root:
            parent[key], key = root, parent[key]
        return root

    for a, b in _pair_keys(pairs):
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            low, high = sorted((root_a, root_b))
            parent[high] = low
    return {key: find(key) for key in parent}


def property_group_rows(
    listing: pa.Table, pairs: pa.Table, *, snapshot_id: int, built_at: datetime
) -> pa.Table:
    """One row of listing_property_group per listing with a detail record, ordered by key.

    A listing in no pair is its own group of one. A group is complete when every two of its n
    members pair, that is when it holds n * (n - 1) / 2 pairs; otherwise a chain holds it together.
    """
    keys = sorted(
        (row["source"], row["source_listing_id"])
        for row in listing.select(["source", "source_listing_id", "detail_observed_at"]).to_pylist()
        if row["detail_observed_at"] is not None
    )
    group = group_of(pairs)
    root_of = {key: group.get(key, key) for key in keys}
    sizes = Counter(root_of.values())
    pair_counts = Counter(group[a] for a, _ in _pair_keys(pairs))
    roots = [root_of[key] for key in keys]
    return pa.Table.from_pydict(
        {
            "source": [source for source, _ in keys],
            "source_listing_id": [listing_id for _, listing_id in keys],
            "property_group_id": [f"{source}:{listing_id}" for source, listing_id in roots],
            "group_size": [sizes[root] for root in roots],
            "group_is_complete": [
                pair_counts[root] == sizes[root] * (sizes[root] - 1) // 2 for root in roots
            ],
            "property_group_version": [PROPERTY_GROUP_VERSION] * len(keys),
            "silver_listing_snapshot_id": [snapshot_id] * len(keys),
            "built_at": [built_at.astimezone(UTC)] * len(keys),
        },
        schema=PROPERTY_GROUP_SCHEMA,
    )


class PropertyGroupError(Exception):
    """The Silver catalog cannot be read as the build needs it."""


@dataclass(frozen=True)
class BuildReport:
    """Counts of one build; groups are those of two or more listings."""

    listings: int
    groups: int
    surplus: int
    incomplete_groups: int
    groups_above_five: int
    largest_group: int
    snapshot_id: int


def build(catalog: Catalog, out: str, *, now: Callable[[], datetime]) -> BuildReport:
    """Build listing_property_group from the current snapshot of Silver's listing into ``out``.

    ``out`` is a directory or an ``s3://`` location that already names the versioned Gold
    prefix; the file is ``<out>/listing_property_group/part-0.parquet``. The snapshot id is taken
    once and the scan reads exactly that snapshot, so the recorded id is the one read even if
    Silver commits meanwhile.
    """
    identifier = f"{NAMESPACE}.listing"
    if not catalog.table_exists(identifier):
        raise PropertyGroupError(f"the catalog has no table {identifier}")
    table = catalog.load_table(identifier)
    snapshot = table.current_snapshot()
    if snapshot is None:
        raise PropertyGroupError(f"the table {identifier} has no snapshot")
    listing = table.scan(
        snapshot_id=snapshot.snapshot_id, selected_fields=LISTING_COLUMNS
    ).to_arrow()
    with duckdb.connect() as connection:
        pairs = duplicate_pairs(connection, listing)
    rows = property_group_rows(listing, pairs, snapshot_id=snapshot.snapshot_id, built_at=now())
    _write(rows, out)
    return _report(rows, snapshot.snapshot_id)


def _write(rows: pa.Table, out: str) -> None:
    location = out if "://" in out else str(Path(out).resolve())
    filesystem, root = FileSystem.from_uri(location)
    directory = f"{root.rstrip('/')}/{DATASET}"
    filesystem.create_dir(directory, recursive=True)
    pq.write_table(rows, f"{directory}/part-0.parquet", filesystem=filesystem)


def _report(rows: pa.Table, snapshot_id: int) -> BuildReport:
    sizes = Counter(rows.column("property_group_id").to_pylist())
    incomplete = {
        row["property_group_id"]
        for row in rows.select(["property_group_id", "group_is_complete"]).to_pylist()
        if not row["group_is_complete"]
    }
    groups = [size for size in sizes.values() if size > 1]
    return BuildReport(
        listings=rows.num_rows,
        groups=len(groups),
        surplus=sum(size - 1 for size in groups),
        incomplete_groups=len(incomplete),
        groups_above_five=sum(1 for size in groups if size > 5),
        largest_group=max(sizes.values(), default=0),
        snapshot_id=snapshot_id,
    )
