"""Write Silver tables to an Iceberg catalog.

Three writers, each refusing a table whose columns drifted: a whole overwrite (backfill), a
replacement of whole day partitions, and an upsert keyed by listing with deletes, all in one
transaction per table so a failed write leaves the table unchanged.
"""

from collections.abc import Collection, Mapping, Sequence
from datetime import date
from functools import reduce

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.expressions import And, BooleanExpression, EqualTo, In, Or
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform

PARTITION_COLUMN = "observed_date"


class SilverSchemaError(Exception):
    """A rebuild's table no longer matches the Iceberg table's schema; nothing was written."""


def write_silver_tables(
    catalog: Catalog,
    namespace: str,
    tables: Mapping[str, pa.Table],
    properties: Mapping[str, str],
    *,
    allow_new_columns: bool = False,
) -> dict[str, int]:
    """Overwrite ``<namespace>.<name>`` with each Arrow table and return its new snapshot id.

    A missing namespace or table is created from the Arrow schema. ``properties`` go into every
    commit's snapshot summary. The returned id is the table's current snapshot after the commit:
    an overwrite also writes an empty ``DELETE`` snapshot before it, which must never be pinned.
    """
    snapshot_ids: dict[str, int] = {}
    for name, rows in tables.items():
        identifier = f"{namespace}.{name}"
        table, additions = _table_for(catalog, namespace, identifier, rows, allow_new_columns)

        def write(table: Table = table, rows: pa.Table = rows, additions=additions) -> None:
            with table.transaction() as transaction:
                _add_columns(transaction, rows.schema, additions)
                transaction.overwrite(rows, snapshot_properties=dict(properties))

        _commit(identifier, write)
        snapshot_ids[name] = catalog.load_table(identifier).current_snapshot().snapshot_id
    return snapshot_ids


def replace_days(
    catalog: Catalog,
    namespace: str,
    name: str,
    rows: pa.Table,
    days: Collection[date],
    properties: Mapping[str, str],
    *,
    allow_new_columns: bool = False,
) -> int | None:
    """Replace exactly the day partitions ``days`` of ``<namespace>.<name>`` with ``rows``.

    The table is partitioned by ``identity(observed_date)``; an existing unpartitioned table is
    evolved once, which is metadata only, so earlier snapshots stay readable. Rows dated outside
    ``days`` are refused before any commit. Returns the current snapshot id, or ``None`` when
    there is nothing to write and the table does not exist.
    """
    identifier = f"{namespace}.{name}"
    outside = sorted(
        {day.isoformat() for day in rows.column(PARTITION_COLUMN).to_pylist()}
        - {day.isoformat() for day in days}
    )
    if outside:
        raise ValueError(f"{identifier}: rows dated {', '.join(outside)} are not in the named days")
    if not days:
        return _current_snapshot_id(catalog, identifier)
    table, additions = _table_for(catalog, namespace, identifier, rows, allow_new_columns)
    named = In(PARTITION_COLUMN, sorted(day.isoformat() for day in days))

    def write() -> None:
        with table.transaction() as transaction:
            _add_columns(transaction, rows.schema, additions)
            if PARTITION_COLUMN not in {field.name for field in table.spec().fields}:
                with transaction.update_spec() as spec:
                    spec.add_field(PARTITION_COLUMN, IdentityTransform(), PARTITION_COLUMN)
            transaction.overwrite(rows, named, dict(properties))

    _commit(identifier, write)
    return catalog.load_table(identifier).current_snapshot().snapshot_id


def upsert_rows(
    catalog: Catalog,
    namespace: str,
    name: str,
    rows: pa.Table,
    keys: Sequence[str],
    delete_keys: Collection[tuple[str, ...]],
    properties: Mapping[str, str],
) -> int | None:
    """Upsert ``rows`` on ``keys`` and delete ``delete_keys``, in one transaction.

    Returns the current snapshot id, or ``None`` when the table does not exist and there is
    nothing to write. An upsert that changes nothing writes no snapshot.
    """
    identifier = f"{namespace}.{name}"
    if rows.num_rows == 0 and not delete_keys:
        return _current_snapshot_id(catalog, identifier)
    # Strict: PyIceberg 0.12's upsert fails on files that predate an added column, so new columns
    # reach an upserted table only through a backfill's whole-table write.
    table, _ = _table_for(catalog, namespace, identifier, rows, allow_new_columns=False)

    def write() -> None:
        with table.transaction() as transaction:
            if delete_keys:
                transaction.delete(_key_filter(keys, delete_keys), dict(properties))
            if rows.num_rows:
                transaction.upsert(rows, list(keys), snapshot_properties=dict(properties))

    _commit(identifier, write)
    return _current_snapshot_id(catalog, identifier)


def _table_for(
    catalog: Catalog, namespace: str, identifier: str, rows: pa.Table, allow_new_columns: bool
) -> tuple[Table, list[pa.Field]]:
    """Load or create the table, and decide which columns the rows may add.

    Equal columns add none. With ``allow_new_columns``, rows whose columns contain the table's in
    the same order add the missing ones; anything else, a removed or reordered column, is refused.
    A changed type is refused by the write itself, inside the same transaction, so it adds nothing.
    """
    catalog.create_namespace_if_not_exists(namespace)
    if not catalog.table_exists(identifier):
        return catalog.create_table(identifier, schema=rows.schema), []
    table = catalog.load_table(identifier)
    existing = [field.name for field in table.schema().fields]
    if rows.schema.names == existing:
        return table, []
    if allow_new_columns and _in_order_within(existing, rows.schema.names):
        return table, [field for field in rows.schema if field.name not in existing]
    _check_column_names(identifier, table, rows)
    raise AssertionError("unreachable: differing columns always raise")


def _in_order_within(existing: Sequence[str], new: Sequence[str]) -> bool:
    remaining = iter(new)
    return all(name in remaining for name in existing)


def _add_columns(transaction, schema: pa.Schema, additions: Sequence[pa.Field]) -> None:
    # Added at their position in the rows, so the table's column order equals the rows' again.
    if not additions:
        return
    names = schema.names
    with transaction.update_schema() as update:
        update.union_by_name(pa.schema(additions))
        for field in additions:
            position = names.index(field.name)
            if position == 0:
                update.move_first(field.name)
            else:
                update.move_after(field.name, names[position - 1])


def _commit(identifier: str, write) -> None:
    try:
        write()
    except ValueError as error:
        raise SilverSchemaError(f"{identifier} refused the rows' schema: {error}") from error


def _current_snapshot_id(catalog: Catalog, identifier: str) -> int | None:
    if not catalog.table_exists(identifier):
        return None
    snapshot = catalog.load_table(identifier).current_snapshot()
    return snapshot.snapshot_id if snapshot else None


def _key_filter(keys: Sequence[str], values: Collection[tuple[str, ...]]) -> BooleanExpression:
    # Group by all key columns but the last, so a delete of many listings of one source is one
    # In() rather than one clause per listing.
    *prefix, last = keys
    groups: dict[tuple[str, ...], list[str]] = {}
    for value in values:
        groups.setdefault(tuple(value[:-1]), []).append(value[-1])
    clauses = [
        reduce(
            And, [*(EqualTo(k, v) for k, v in zip(prefix, group, strict=True)), In(last, members)]
        )
        if prefix
        else In(last, members)
        for group, members in groups.items()
    ]
    return reduce(Or, clauses)


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
