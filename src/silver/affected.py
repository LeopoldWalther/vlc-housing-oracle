"""The listings a run must recompute: everything a replaced day held before or holds now."""

import pyarrow as pa

ListingKey = tuple[str, str]


def affected_listings(previous: pa.Table, new: pa.Table) -> set[ListingKey]:
    """Union of ``(source, source_listing_id)`` over Silver's previous rows and the new rows.

    The previous rows matter as much as the new ones: a listing that vanishes from a rewritten day
    must be recomputed too, or its first and last seen and its chosen record would go stale.
    """
    return {
        (row["source"], row["source_listing_id"])
        for table in (previous, new)
        for row in table.select(["source", "source_listing_id"]).to_pylist()
    }
