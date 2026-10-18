# ADR 0002 — Incremental processing by partition, with manifests as the change log

**Status:** Superseded by [ADR 0003](0003-immutable-bronze-and-landing-per-run.md) on 2026-10-18 · **Date:** 2026-10-17 · **Amends:** [ADR 0001](0001-silver-on-iceberg.md)

## Context

ADR 0001 and FEATURE-001 rebuild Silver whole from landing, and landing is rebuilt whole from
Bronze. That was chosen for size: on 2026-10-15 Bronze held **1,791 objects, 86 MB** after three
weeks of collection, and landing held 50,461 observations over 14 days.

The operator wants the platform to run for years. At the current rate, five years mean roughly
**155,000 Bronze objects (≈ 7.5 GB)** and **≈ 6.5 million observations**:

- A whole landing rebuild would then read every Bronze object every night: minutes to tens of
    minutes, and S3 GET requests growing without bound.
- A whole Silver rebuild would still take only seconds to a minute, but it reprocesses every day of
    history to add one.

Processing the same unchanged files again and again is the cost to remove.

Two facts of this corpus constrain any incremental design:

- **Old Bronze partitions change.** A replay re-derives stored payloads into the partition of their
    capture date. "Everything after the last date processed" would therefore miss corrections to
    earlier days. "New" has to mean *written since the last run*, not *dated after it*.
- **Some Silver tables span all of history.** `observation` and `walk` are per day. `listing` (newest
    detail record, first and last seen, days observed) and `listing_text` depend on a listing's whole
    history.

## Decision

1. **The unit of work is a day partition (`observed_date`), replaced whole.** A run determines which
    days changed and rewrites exactly those days in its output. Rewriting a day twice gives the same
    result, so retries and duplicate triggers are harmless. Nothing is appended row by row.

2. **Landing v2 is partitioned by day and announces each run with an immutable manifest.**
    - Data lives at `landing/v2/<table>/observed_date=<YYYY-MM-DD>/part-0.parquet`: exactly one file
        per table and day, replaced by a single PUT, so a reader never sees half a day.
    - After its data, each landing run writes `landing/v2/_manifests/<manifest_id>.json` listing the
        days it rewrote per table. Manifest ids sort by time. Manifests are never changed or deleted.
    - The manifests are the change log. A consumer that missed runs processes every manifest after
        its own checkpoint, so nothing is lost when a night fails.
    - The contract is [`contracts/landing/v2/`](../../contracts/landing/v2/). Landing v1 keeps being
        written beside v2 until Silver has moved, then stops.

3. **Progress is stored with the data it describes, in the same write.**
    - Landing's own checkpoint (how far it has read Bronze) is a field of its latest manifest.
    - Silver's checkpoint is the snapshot property `landing_manifest_id` on every commit: data and
        checkpoint land in one Iceberg commit, or neither does.
    - **No separate state store (DynamoDB) is used for checkpoints.** A store beside the data cannot
        be written atomically with S3 or Iceberg. A crash between the two leaves the checkpoint
        claiming work that is not there, or the reverse.

4. **Silver applies a manifest per table shape.**
    - `walk` and `observation` are Iceberg tables partitioned by `identity(observed_date)`. The
        changed days are replaced with `overwrite(overwrite_filter=observed_date IN changed_days)`.
    - `listing` and `listing_text` are updated with `upsert` for the listings that appear in a
        changed day. Their history-wide columns (newest record, first and last seen, days observed)
        are recomputed for exactly those listings from their complete history.
    - Every commit carries `landing_manifest_id`, `transformation_version` and `redaction_version`.

5. **A full rebuild remains, as a deliberate backfill.** A change of meaning, such as a new
    `transformation_version`, a redaction `r2` or a new dedupe rule, cannot be applied
    incrementally to history. The whole-history path (FEATURE-001's `rebuild`) stays, runs only when
    started on purpose, and records the newest manifest it covered as its checkpoint. A first run
    with no checkpoint is a backfill.

ADR 0001 otherwise stands: Silver is Iceberg, Gold is plain Parquet, and a training dataset pins a
Silver snapshot.

## Consequences

- Nightly work is proportional to what changed, typically one or two days, not to history.
- The landing contract gains a layout and a manifest. Both repositories pin the new contract, and the
    private repository writes v1 and v2 side by side for a transition.
- Correctness now depends on the change log being complete, so the landing writer must expand its set
    of affected days in two cases:
    - when a Bronze object changes;
    - when a collection run spans midnight, to every day holding a report of that run, because
        `run_ended_at` sums across the run.
- Silver performs more operations per night and so writes more snapshots per table. Each snapshot
    carries the manifest id, so any of them identifies its input. Daily partitions mean small files
    (≈ 3,600 observations a day). That is acceptable at this volume, and compaction can wait for a
    backfill or a later maintenance job.
- Month partitioning was rejected for now: PyIceberg 0.12 needs the native `pyiceberg-core` package
    to write a `month` transform. It failed with `NotInstalledError` in a 2026-10-17 spike, while
    `identity(observed_date)` writes without it.
- The rules in `CLAUDE.md` and the README that said "rebuilt whole" now say "incremental by
    partition, with a full rebuild as backfill".

## Evidence

Spike on 2026-10-17 with PyIceberg 0.12.0 and a local SQLite catalog:

- On a table partitioned by `identity(observed_date)` holding five days, an `overwrite` filtered to
    one day replaced exactly that day (1 row instead of 2) and left the other four untouched.
- `upsert(join_cols=["id"])` on a table with a `list<string>` column updated one row and inserted
    another correctly.
- Every snapshot written by these operations carried the given `snapshot_properties`.

## Alternatives considered

- **Keep whole rebuilds.** This is the simplest option, and cheap for about two more years on Silver.
    Rejected for landing first: its input, Bronze object count, grows fastest, and changing the
    landing contract later costs more than now.
- **DynamoDB as the checkpoint store.** It works and the private repository already uses DynamoDB,
    but it is not atomic with the data (Decision 3). It becomes worth it only if many jobs share
    state.
- **Date watermark ("process days after the last one").** Rejected: replays rewrite old days.
- **Landing as Iceberg, read through snapshot diffs.** This is the standard lakehouse change feed,
    but it puts a catalog and PyIceberg into the private repository and makes the contract
    catalog-shaped instead of file-shaped. The manifest gives the same change list with plain files.
- **One event per Bronze object, through a queue.** It is fine-grained but turns a nightly batch into
    a stream, with ordering and deduplication duties this volume does not need.
