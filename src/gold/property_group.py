"""Property groups: listings that are certainly the same property, under rule pg1."""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

SQL_DIR = Path(__file__).resolve().parent / "sql"

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
    connection.register("gold_listing", listing)
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
