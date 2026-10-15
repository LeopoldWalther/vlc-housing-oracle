"""Write Silver tables to an Iceberg catalog: one overwrite, and so one pinnable snapshot, each."""

from collections.abc import Mapping

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.table import Table


class SilverSchemaError(Exception):
    """A rebuild's table no longer matches the Iceberg table's schema; nothing was written."""


def write_silver_tables(
    catalog: Catalog,
    namespace: str,
    tables: Mapping[str, pa.Table],
    properties: Mapping[str, str],
) -> dict[str, int]:
    """Overwrite ``<namespace>.<name>`` with each Arrow table and return its new snapshot id.

    A missing namespace or table is created from the Arrow schema. ``properties`` go into every
    commit's snapshot summary. The returned id is the table's current snapshot after the commit:
    an overwrite also writes an empty ``DELETE`` snapshot before it, which must never be pinned.
    """
    catalog.create_namespace_if_not_exists(namespace)
    snapshot_ids: dict[str, int] = {}
    for name, rows in tables.items():
        identifier = f"{namespace}.{name}"
        if catalog.table_exists(identifier):
            table = catalog.load_table(identifier)
            _check_column_names(identifier, table, rows)
        else:
            table = catalog.create_table(identifier, schema=rows.schema)
        try:
            table.overwrite(rows, snapshot_properties=dict(properties))
        except ValueError as error:
            raise SilverSchemaError(
                f"{identifier} refused the rebuild's schema: {error}"
            ) from error
        snapshot_ids[name] = table.current_snapshot().snapshot_id
    return snapshot_ids


def _check_column_names(identifier: str, table: Table, rows: pa.Table) -> None:
    # PyIceberg's own check rejects extra columns and changed types, but writes a missing column as
    # nulls. A lost column is a defect in the transformation, never a valid rebuild.
    existing = [field.name for field in table.schema().fields]
    if rows.schema.names == existing:
        return
    missing = [name for name in existing if name not in rows.schema.names]
    extra = [name for name in rows.schema.names if name not in existing]
    raise SilverSchemaError(
        f"{identifier}: columns differ from the table's schema "
        f"(missing: {missing or 'none'}, extra: {extra or 'none'}, order may differ)"
    )
