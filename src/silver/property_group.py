"""Property groups: listings that are certainly the same property, under rule pg1.

The rule and its rows for the Silver table ``listing_property_group``. The functions are pure;
the run in ``src.silver.rebuild`` reads Silver's listing, calls them and writes the table.
"""

from pathlib import Path

import duckdb
import pyarrow as pa

SQL_DIR = Path(__file__).resolve().parent / "sql"

PROPERTY_GROUP_TABLE = "listing_property_group"

# The columns of Silver's listing that the rule and the rows read.
PROPERTY_GROUP_LISTING_COLUMNS = (
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

PROPERTY_GROUP_VERSION = "pg1"
PRICE_GAP_MAX_PERCENT = 5
DISTANCE_MAX_METERS = 350


def duplicate_pairs(connection: duckdb.DuckDBPyConnection, listing: pa.Table) -> pa.Table:
    """Every pair of listings that rule pg1 calls the same property, each once, smaller key first.

    ``listing`` needs ``PROPERTY_GROUP_LISTING_COLUMNS``. Columns of the result: source_a,
    source_listing_id_a, source_b, source_listing_id_b.
    """
    connection.execute(f"SET VARIABLE price_gap_max_percent = {PRICE_GAP_MAX_PERCENT}::BIGINT")
    connection.execute(f"SET VARIABLE distance_max_meters = {DISTANCE_MAX_METERS}")
    connection.register("property_group_listing", listing.select(PROPERTY_GROUP_LISTING_COLUMNS))
    try:
        return connection.execute(
            (SQL_DIR / "pairs.sql").read_text(encoding="utf-8")
        ).to_arrow_table()
    finally:
        connection.unregister("property_group_listing")
