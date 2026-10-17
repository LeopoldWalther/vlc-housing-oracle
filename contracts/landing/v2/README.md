# Landing contract v2

**Status:** proposed by [ADR 0002](../../../docs/adr/0002-incremental-processing.md). It is frozen
once both repositories pin the digest below: this repository in FEATURE-003, the acquisition
repository in its FEATURE-015. Until then it may still change. Afterwards a change is `landing/v3`.

Landing v2 carries **the same four tables with the same columns as [v1](../v1/README.md)**; read
that document for what each row means. What v2 adds is a **layout partitioned by day** and an
**immutable manifest per landing run**, so a reader processes only what changed.

## Identity of the contract

- [`schema.json`](schema.json) declares the layout, the manifest fields and every table's columns.
    Its SHA-256 is `32f5ca67585da0784fc9c7f6b482f89ae4baa74846d4cf37418d826cf5f8387d`.
- A table file conforms when `DESCRIBE SELECT * FROM read_parquet(<file>, hive_partitioning = false)`
    returns the table's `(column_name, column_type)` list, in order, exactly as in v1.

## Layout

```
landing/v2/
├── walk/observed_date=2026-10-15/part-0.parquet
├── walk/observed_date=2026-10-16/part-0.parquet
├── observation/observed_date=…/part-0.parquet
├── listing_detail/observed_date=…/part-0.parquet
├── listing_text/observed_date=…/part-0.parquet
└── _manifests/20261017T003105Z-3f9a1c2e.json
```

- **Exactly one file per table and day,** always named `part-0.parquet`. A writer replaces a day by
    one PUT of that object, so a reader sees the old day or the new day, never a mixture.
- **A day is always rewritten whole,** never appended to. A day that has no rows for a table holds a
    zero-row file.
- **The path's `observed_date` equals the column inside the file.** The column is authoritative:
    readers read with `hive_partitioning = false` and use the path only to address a day.

## Manifests: the change log

After a landing run has written every day it changed, it writes one manifest. Manifests are
**never modified or deleted**.

```json
{
  "contract": "landing",
  "version": "v2",
  "schema_sha256": "32f5ca67585da0784fc9c7f6b482f89ae4baa74846d4cf37418d826cf5f8387d",
  "manifest_id": "20261017T003105Z-3f9a1c2e",
  "written_at": "2026-10-17T00:31:05Z",
  "partitions": {
    "walk": ["2026-10-16"],
    "observation": ["2026-10-15", "2026-10-16"],
    "listing_detail": ["2026-10-16"],
    "listing_text": ["2026-10-16"]
  },
  "row_counts": {
    "walk": {"2026-10-16": 48},
    "observation": {"2026-10-15": 3604, "2026-10-16": 3571},
    "listing_detail": {"2026-10-16": 212},
    "listing_text": {"2026-10-16": 212}
  },
  "source_watermark": "<opaque to readers>"
}
```

- `manifest_id` is `<UTC instant as YYYYMMDDTHHMMSSZ>-<8 lowercase hex>`. **Lexicographic order is
    write order.**
- `partitions` lists, per table, every day this run rewrote. A table missing from the object, or
    with an empty list, was not touched.
- `row_counts` holds the row count of each rewritten file at the moment it was written. A reader can
    compare it with what it reads.
- `source_watermark` belongs to the writer: how far it has read its own input. Readers ignore it.
- A writer may add fields; readers ignore fields they do not know.

## How a reader processes landing v2

1. Keep a checkpoint: the last `manifest_id` processed, stored with the reader's own output (Silver
    stores it as the snapshot property `landing_manifest_id`).
2. List `landing/v2/_manifests/` and take every manifest whose id sorts after the checkpoint, in
    order. With no checkpoint, read every partition: that is a backfill.
3. Take the union of the days they name per table, and read those days' **current** files. A newer
    rewrite of the same day supersedes an older one, so reading the current file is always correct.
4. Commit the result together with the newest `manifest_id` read as the new checkpoint.

Missing a night costs nothing: the next run finds both manifests.

## What the writer guarantees

- Every day whose rows could have changed is in the manifest. That includes days rewritten because an
    old input changed (a replay re-derives earlier days), and every day holding a report of a
    collection run that spans midnight, because `run_ended_at` sums over the whole run.
- Within a day, the key uniqueness and the `listing_detail`/`listing_text` pairing of v1 hold.
- A failed run writes no manifest. Days it already replaced are still correct: they are a later
    rewrite of the same day, and the next successful manifest names them again.

## Transition from v1

The writer produces v1 (whole files) and v2 side by side until this repository's Silver reads v2,
then stops writing v1. v1's files and contract stay as they are.
