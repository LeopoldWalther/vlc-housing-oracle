# ADR 0003 — Immutable Bronze, and landing per collection run

**Status:** Accepted · **Date:** 2026-10-18 · **Supersedes:** [ADR 0002](0002-incremental-processing.md) · **Amends:** [ADR 0001](0001-silver-on-iceberg.md)

## Context

ADR 0002 made processing incremental by day partition and introduced landing v2, whose writer found
changed Bronze objects by their `LastModified` time inside a window with a safety lag. It was
implemented, deployed and verified within three days. Reviewing the whole flow from scraping to
Silver before anything read it showed that every awkward piece of that design had one cause:

**Bronze objects were rewritten in place.** The acquisition job merged each page's rows into one
object per area and day, and a replay merged into old days. Because an object could change after
it was written, no consumer could know what was new without guessing, and the guess needed:

- a time window with a 15-minute lag, in case an object was still being written;
- a weekly full run as a safety net, in case the window missed one;
- and that weekly run announced every day, which would have made an incremental Silver rebuild
    its whole history once a week.

The merge also discarded the history of earlier runs on the same day, which is why events could
only ever be derived per area-day, never per run.

## Decision

1. **Nothing up to and including landing is ever rewritten.** Every object is written exactly once.
    The acquisition job writes one object per page and area under the collection run's own prefix,
    one report per area after that area's last page, and one run marker at exit, in a `finally`
    block, with the run's outcome. Overwrites are refused by the storage (`If-None-Match: *`),
    not merely avoided by discipline.

2. **The unit of work downstream is the collection run.** A run is finished when its marker
    exists, or when it started more than a configured timeout ago (derived from the job's own
    runtime cap, passed in from infrastructure, never a constant in code). Landing lands every
    finished run that has no landing commit, writes its four files and then its commit. The
    contract is [`contracts/landing/v3/`](../../contracts/landing/v3/README.md); every table gains
    `run_id`.

3. **Pending work is a set difference, never a watermark.** Landing: finished runs minus committed
    runs. Silver: landing commits minus its own ingest log, an Iceberg table written last in the
    same run. Order is irrelevant, a repeat is harmless, and two concurrent runs of a stage write
    identical files and tolerate each other's commit.

4. **The trigger is the data, not the compute.** The run marker's S3 `Object Created` event starts
    the landing job; the landing commit's event will start Silver. Neither stage depends on where
    or how the stage before it runs, and a stage cannot trigger itself, because it writes under a
    different prefix.

5. **The "never triggered" case is covered twice, cheaply.** Each stage publishes the number of
    runs still pending as a metric with an alarm, and a daily schedule invokes the same idempotent
    job as a catch-up. This is not a reprocessing safety net: nothing already committed is touched.

6. **A rule change is still a deliberate full backfill**, as in ADR 0002 decision 5. Silver's
    whole-history path stays; it records every landing run id it covered in the ingest log.

ADR 0001 otherwise stands: Silver is Iceberg, Gold is plain Parquet, a training dataset pins a
Silver snapshot. ADR 0002's decisions 3 and 4 stand in spirit: progress is stored with the data,
and Silver still replaces whole day partitions, now the union of `observed_dates` of the pending
commits, re-derived from all of landing because several runs can touch one day.

## Consequences

- The acquisition repository migrates its corpus once (an idempotent import into immutable runs)
    and retires landing v2 before it ever had a reader.
- More, smaller files: one per page rather than one per area-day. Readers address runs, not
    files, so compaction can be added later without a contract change.
- Events per collection run become possible in Silver, because runs are no longer merged.
- Landing v2 and its contract are superseded. The v2 documents stay as the record of a decision
    that was corrected after three days, with this ADR saying why.

## Alternatives considered

- **Keep ADR 0002 and add verification to the weekly run** (announce only days whose content
    differs). It fixes the symptom in Silver and leaves the guess in landing. Rejected.
- **Triggering on the acquisition task's stop event** (ECS `STOPPED`). It needs a filter against
    the landing task's own stop, which is the sign of listening to the wrong signal, and it couples
    landing to the acquisition job's runtime. Rejected for the marker's object event.
- **A Step Functions state machine.** Two stages in two repositories, talking through commits, have
    nothing to orchestrate. It earns its place when one run has several dependent steps with their
    own branches and retries.
- **Lambda for the landing job.** Idiomatic for one-minute event-driven work, but a backfill does
    not fit its 15-minute limit, and it would be a second runtime for the same logic.
- **A queue with one message per object.** Fine-grained, but it turns a batch into a stream with
    ordering and deduplication duties this volume does not need. The set difference over run ids
    gives exactly-once at no cost.
- **Landing as Iceberg with snapshot diffs.** Rejected as in ADR 0002: it puts a catalog into the
    acquisition side and makes the contract catalog-shaped. Commits give the same change list with
    plain files.
