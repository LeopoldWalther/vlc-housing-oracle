"""Property groups: listings that are certainly the same property, under rule pg1."""

from pathlib import Path

import duckdb
import pyarrow as pa

SQL_DIR = Path(__file__).resolve().parent / "sql"

ListingKey = tuple[str, str]

PROPERTY_GROUP_VERSION = "pg1"
PRICE_GAP_MAX_PERCENT = 5
DISTANCE_MAX_METERS = 350


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
