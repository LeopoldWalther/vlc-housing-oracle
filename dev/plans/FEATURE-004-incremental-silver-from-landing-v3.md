# FEATURE-004 — Incremental Silver from landing v3, run by AWS

**Status:** 🔵 Planned · **Effort:** L (~28h) · **Priority:** High
**Branch root:** `feature/incremental-silver-v3` · **Created:** 2026-10-18 · **Updated:** 2026-10-19

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-004.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-004-technical-plan.yaml`.

## Objective

Silver follows landing v3 the way landing follows Bronze: each run processes exactly the collection
runs it has not processed yet, found by a set difference between landing's commits and Silver's own
ingest log, replaces the day partitions those runs touch, recomputes the listings those days
affect, and records the runs in the ingest log last. On AWS a commit's event is queued, and a
single-consumer FIFO queue starts one Silver task at a time, so two runs can never overlap; a daily
catch-up queues the same message, and a backfill remains a deliberate whole rebuild. After this
feature nothing reads landing v1.

## Context

- **Decision record:** [ADR 0003](../../docs/adr/0003-immutable-bronze-and-landing-per-run.md),
    which supersedes ADR 0002, and the contract [`contracts/landing/v3/`](../../contracts/landing/v3/README.md)
    (`schema.json` SHA-256 `0ccda5843a11ec12b6d6ecf8cb9f8301c0df41a3564122b4c4bd78ce0672e61b`, pinned
    by the acquisition repository since its FEATURE-016). FEATURE-003 planned the same Silver side
    against landing v2 and is superseded; its day-replacement and affected-listing design carries
    over, its manifest checkpoint does not.
- **Review of 2026-10-18** ([REVIEW-FEATURE-004](../reviews/REVIEW-FEATURE-004.md), Changes
    Recommended): a spike showed that PyIceberg 0.12 **retries and rebases** a conflicting commit
    instead of failing it, so two overlapping Silver runs with different pending sets could both
    commit and leave `listing` computed without one run's days (H1). The user chose to serialize
    runs through an SQS FIFO queue with a single consumer (2026-10-19), over Step Functions and
    over an S3 lease. M1–M3 and L1–L4 of that review are folded into this revision.
- **Current state:**
    - FEATURE-001 rebuilds Silver whole from a local copy of `landing/v1/`:
        `register_landing` checks the v1 contract and registers DuckDB views, `build_tables` runs
        the statements in `src/silver/sql/` (`walk`, `observation`, `chosen_detail`, `listing`,
        `listing_text`, `redact`), and `write_silver_tables` overwrites four unpartitioned Iceberg
        tables in the Glue database `vlc_housing_silver` with snapshot properties
        (`transformation_version`, `redaction_version`, `landing_contract_sha256`,
        `rebuild_started_at`). The operator ran it once.
    - `src/synthetic/landing.py` generates a v1 landing with the hard cases (replays, a run across
        midnight, padding cards, duplicate sightings, personal data in a description).
    - `infra/silver/` holds one Glue database. No compute, no trigger, no image exists here.
    - FEATURE-001's 81 tests build their fixtures with the v1 writer `write_landing_tables` and
        exercise `register_landing` over v1 files; they are the only tests of the dedupe orders,
        the empty-card rule, redaction `r1` and the boundary checks.
    - **Landing v3 is live** in the operator's bucket since 2026-10-18: one partition per
        collection run under `landing/v3/<table>/run_date=<d>/run=<run_id>/part-0.parquet` and one
        commit per run under `landing/v3/_commits/<run_id>.json`, written by the acquisition
        repository's landing task after every collection run. Its backfill landed 144 runs; the
        union of v3 equals v1 row for row minus `run_id`.
- **Problem:** Silver still rebuilds all of history from a whole file nobody writes any more
    unless asked. It has no checkpoint, no trigger and no catch-up, and its `observation` rule
    assumes one row per listing-day per area, which v3 no longer guarantees: two runs of one day
    both keep their rows.
- **Constraints:**
    - This repository is public: no real data, bucket names, account ids or portal names in code,
        fixtures or docs. Every real name is a Terraform variable or a placeholder.
    - This repository never gets a role in the operator's AWS account (its CI does not deploy).
        The image is public, pulled without credentials; Terraform is applied by the operator.
    - Every Silver commit must remain pinnable and traceable to the landing runs that built it.
    - GitHub-visible activity on weekdays only after 17:00 Europe/Berlin.

## Scope

- **In scope:**
    - The landing v3 contract pinned here; the synthetic generator writes v3 (runs and commits).
    - Silver's checkpoint as an Iceberg table, `landing_ingest`, written last.
    - Per-day Silver tables (`walk`, `observation`, and a new `listing_detail`) partitioned by
        `observed_date` and replaced per day; `listing` and `listing_text` recomputed for the
        affected listings and upserted.
    - The rule for several runs touching one listing-day.
    - Incremental and backfill modes on the CLI, reading v3 from a local directory or from S3.
    - A public image, a Fargate task, and the serialized trigger: an EventBridge rule queueing
        commit events into an SQS FIFO queue, a Lambda consumer with concurrency one that runs the
        task and waits for it, a dead-letter queue with an alarm, a daily catch-up that queues the
        same message, a pending metric with an alarm, failure mail: all as code under
        `infra/silver/`.
    - Migration of every FEATURE-001 rule test to the v3 fixtures, so no coverage is lost.
    - The first backfill and the first event-started run, by the operator.
    - Retiring the v1 path: `register_landing` over v1 files and the v1 generator go; the v1
        contract copy stays pinned as the record.
- **Out of scope:**
    - `listing_event` (FEATURE-002) and Gold.
    - Snapshot expiry and compaction (set when a dataset first pins a snapshot).
    - Changing the landing contract.
- **Users / consumers:** the operator, and every later feature that reads Silver.

## Dependencies

- **Needs:** FEATURE-001 (the Silver rules and the sink); ADR 0003 and `contracts/landing/v3/`
    on `main`; the acquisition repository's FEATURE-016 (landing v3 written in the bucket), done.
- **Supersedes:** FEATURE-003.
- **Unblocks:** FEATURE-002 (`listing_event` per run becomes possible, because runs are no longer
    merged).

## Open assumptions

- **Assumption 1 — PyIceberg 0.12 replaces one day of an `identity(observed_date)`-partitioned
    table with `overwrite(overwrite_filter=...)`, and `upsert` updates rows with list columns.**
    — **Evidence:** the ADR 0002 spike of 2026-10-17 (SQLite catalog, PyIceberg 0.12.0): an
    overwrite filtered to one day replaced exactly that day; `upsert(join_cols=["id"])` on a table
    with a `list<string>` column updated one row and inserted another; snapshot properties were
    kept. — **If wrong:** per-day replacement falls back to a delete-then-append of the day, which
    is two snapshots per day; the equality test (task 3.3) still holds.
- **Assumption 2 — the same listing-day appears in several landing v3 runs, and the latest
    sighting is the one v1 kept.** — **Evidence:** the acquisition repository's Bronze v1 merged
    by `(listing_id, observed_date)` with the newest value winning, and its FEATURE-016 import
    proved v3's union equals v1 row for row because every v1 row came from exactly one run. From
    now on a day with two live runs (00:01 and 06:00, as on 2026-09-30) holds both runs' rows.
    — **If wrong:** nothing breaks; the rule in Design is a total order and a test pins it.
- **Assumption 3 — DuckDB in Fargate reads `s3://.../landing/v3/` through `httpfs` under the
    task role.** — **Evidence:** the acquisition repository's landing task does exactly this
    since 2026-10-18 (assumption 3 of its FEATURE-016 held). — **If wrong:** the first manual
    `run-task` fails on the read; fallback: download the needed files through boto3 to a
    temporary directory, which the local mode already does for tests.
- **Assumption 4 — Fargate pulls a public GHCR image without credentials.** — **Evidence:**
    unvalidated here; it is the documented behaviour of public registries. — **If wrong:** the
    task's execution role gets a pull-through cache in ECR, which the operator creates by hand
    outside this repository.
- **Assumption 5 — concurrent PyIceberg commits do not fail; they are rebased and retried, and
    correctness must never rely on a commit failing.** — **Evidence:** the review spike of
    2026-10-18: two handles loaded from the same state both appended; the second logged
    `Commit failed due to a concurrent update, retrying (1/4)` and succeeded, leaving two rows.
    — **Consequence:** overlap is prevented upstream by the FIFO queue (Design), not detected
    downstream. Idempotent writes (whole-day replacement, upserts keyed by listing, an ingest log
    upserted by `run_id`) make a repeated run harmless; they do not make an overlapping run safe.
- **Assumption 7 — one incremental Silver run finishes well inside the Lambda consumer's
    15-minute limit.** — **Evidence:** the acquisition landing task landed 144 runs in 3 min 22 s,
    and a Silver night touches one or two days; FEATURE-001's whole rebuild took seconds locally.
    Not measured in Fargate. — **If wrong:** the consumer times out, the message is redelivered
    and the next attempt finds the same pending set (the task is repeated, which is harmless); after
    three attempts the dead-letter alarm fires. A backfill never goes through the queue.
- **Assumption 8 — a batch that fails three times in a row signals a defect, not transient
    failure, so stopping the retry loop there loses nothing.** — **Evidence:** every start repeats
    the same deterministic work over immutable landing, so a third identical failure has the same
    cause as the first; transient AWS errors are covered by the consumer's own retries inside one
    invocation and by the daily catch-up. — **If wrong:** a transient outage longer than three
    deliveries (about 48 minutes) parks the message, the alarm fires, and the catch-up at 05:30
    repeats the work the next morning; nothing is lost.
- **Assumption 6 — the commit index stays small enough to list on every run.** — **Evidence:**
    about eight collection runs a day: 3,000 commits a year, one paginated listing of a few
    pages. The ingest log keeps each run's days, so only pending commits are read. — **If
    wrong:** list by `run_date` prefixes for the last N days, as landing does.

## Design

- **Primary approach:** keep FEATURE-001's SQL as the rules and change what they run over: from
    "all of landing" to "the runs that touch the named days" for the per-day tables, and from "all
    listings" to "the affected listings" for the history-wide tables. One code path serves both
    modes; a backfill is incremental with every run pending and every day named.
- **Data flow of one run:**

    ```
    commits    = landing/v3/_commits/*.json                                    (listed, paginated)
    ingested   = run_id → observed_dates from the ingest log                   (one Iceberg scan)
    pending    = commits − ingested, oldest first                               (none → exit 0, "nothing pending")
    days[t]    = ∪ observed_dates[t] of the pending commits
    runs[t][d] = every run (pending or ingested) whose observed_dates[t] contains d
    read       landing/v3/<t>/run_date=…/run=<r>/part-0.parquet for r in runs[t][d], d in days[t]
    check      v3 columns per file; row_counts per the commit; nulls, pairing (per run)
    per day    walk, observation, listing_detail ← SQL over the read rows, newest sighting rule
               commit each: overwrite(filter observed_date IN days[t])
    affected   = (source, source_listing_id) in Silver's previous rows of days[t] ∪ the new rows
    listing    = recompute for affected ids from Silver observation + Silver listing_detail
    text       = the chosen record's landing listing_text row, redacted r1
               upsert listing, upsert listing_text on (source, source_listing_id)
    ingest log ← upsert one row per pending run on run_id, written last
    every commit: landing_run_count, landing_run_first, landing_run_last (the pending ids'
                  count and extremes; the ingest log holds the list), transformation_version,
                  redaction_version, landing_contract_sha256
    ```

- **Contracts and invariants:**
    - **Silver tables** in `vlc_housing_silver`:
        - `walk`, `observation` and the new `listing_detail` are partitioned by
            `identity(observed_date)`; `listing_detail` carries every landing `listing_detail`
            column plus `run_id` and `transformation_version`, one row per landing row after the
            newest-sighting rule.
        - `listing` and `listing_text` are unpartitioned, with FEATURE-001's columns plus
            `run_id` of the chosen record.
        - **`landing_ingest`**: `run_id`, `run_started_at`, `outcome`, `is_replay`,
            `observed_dates` (one list column per table), `commit_written_at`, `ingested_at`,
            `transformation_version`. One row per landing run processed, written with
            `upsert(join_cols=["run_id"])` so a repeated run never duplicates a row. It is the
            checkpoint. `outcome` and `is_replay` are audit columns copied from the commit and are
            **never used in a rule**: markers imported before 2026-10-14 carry `is_replay = false`
            for replays; the rows' own `is_replay` in `walk` is authoritative.
        - FEATURE-001's existing unpartitioned tables are evolved with `update_spec()` on the
            first run of this feature, which is a backfill.
    - **Newest sighting rule** (Assumption 2):
        - `observation`: within one `(source, area, source_listing_id, observed_date)` the row
            with the latest `observed_at`, then latest `ingested_at`, then the run with the
            latest `run_started_at` (from the commit; never the `run_id` string, because
            `import-<date>` ids sort after every timestamp id), then `run_id` as the final
            tiebreaker wins; FEATURE-001's cross-area total order then applies unchanged.
        - `listing_detail` and `listing_text`: within one
            `(source, area, source_listing_id, observed_at)` the row with the latest
            `ingested_at`, then the latest `run_started_at`, then `run_id` wins, which is what a
            replay's re-derivation replaced in v1.
    - **Checkpoint rule:** the ingest log is written after every other table of the run. A run
        that fails before it leaves the pending runs pending, and its day replacements and
        upserts are repeated by the next run, which gives the same rows. A run processed twice
        changes nothing.
    - **Serialization rule:** at most one Silver task runs at any time. On AWS this is a property
        of the FIFO queue with one message group and one consumer, not of Silver's code. A
        backfill is started by hand only while the consumer's event source mapping is disabled,
        and the runbook says so.
    - **Text of the chosen record:** `listing_text` is not kept as history in Silver, and the
        chosen detail record of an affected listing can belong to an earlier, already ingested
        run. Its text is therefore read from landing by the chosen record's `run_id`:
        `landing/v3/listing_text/run_date=<d>/run=<run_id>/part-0.parquet`, where `<d>` is the
        date of that run's `run_started_at` from the ingest log, joined on
        `(run_id, area, source_listing_id, observed_at)`. Only the distinct chosen `run_id`s of
        the affected listings are read.
    - **Consistency checks before any commit**, all `LandingContractError` with counts only:
        - every read file conforms to the v3 columns;
        - each read file's row count equals its commit's `row_counts[table]`, so a file replaced
            after its commit is caught;
        - the v1-era rules per run: no nulls in key columns, unique keys within a run,
            `listing_detail`/`listing_text` paired within a run.
    - **Affected listings:** every `(source, source_listing_id)` in Silver's previous rows of the
        replaced days (read before the overwrite) and in the new rows, so a listing that vanishes
        from a rewritten day is recomputed too.
    - **Equality:** incremental through any sequence of commits equals a backfill of the final
        landing, row set by row set, per table (task 3.3).
    - **Backfill:** `--mode backfill` treats every commit as pending and every day as named,
        replaces every partition and upserts every listing, then writes the ingest log for all
        commits. A `transformation_version` or `redaction_version` change is a backfill.
- **Running in AWS** (`infra/silver/`, applied by the operator):
    - **Image:** `python:3.12-slim`, `requirements.txt`, `src/` and `contracts/`, non-root,
        `ENTRYPOINT ["python", "-m", "src.silver.rebuild"]`. Built by a GitHub Actions workflow of
        this repository on every push to `main` and pushed to GHCR under this repository's
        name, tagged with the commit SHA and `latest`. No AWS credential exists in this
        repository.
    - **Task:** its own ECS cluster `vlc-housing-silver`, ARM64, 1 vCPU / 2 GB, image
        `var.silver_image` (an exact SHA tag, never `latest`), environment
        `PYICEBERG_CATALOG__SILVER__TYPE=glue`, `PYICEBERG_CATALOG__SILVER__GLUE__REGION`,
        `AWS_REGION`; default VPC public subnets with a public IP and an egress-only security
        group; command `--landing s3://<lake>/landing/v3 --catalog silver --mode incremental`.
    - **Image:** the Dockerfile also pre-installs DuckDB's `httpfs` and `aws` extensions as the
        runtime user, as the acquisition image does, so a run never downloads an extension.
    - **Roles:** task: `s3:GetObject` and `s3:ListBucket` (prefix-conditioned) on
        `landing/v3/*`; `s3:GetObject`, `PutObject`, `DeleteObject`, `AbortMultipartUpload` and
        `ListBucket` on `silver/*`; Glue `GetDatabase`, `GetTable`, `GetTables`, `CreateTable`,
        `UpdateTable` on the database and its tables; `cloudwatch:PutMetricData` conditioned on
        the namespace `vlc-housing-silver`. Execution: logs only. Consumer (Lambda):
        `ecs:RunTask` on this task definition, `ecs:DescribeTasks` on the cluster, `iam:PassRole`
        on the task's two roles, the SQS receive/delete permissions on the queue, and logs.
        EventBridge and Scheduler: `sqs:SendMessage` on the queue.
    - **Trigger, serialized:**
        1. The lake bucket already publishes its object events to EventBridge (acquisition
            repository). An EventBridge rule on `aws.s3` / `Object Created` with key prefix
            `landing/v3/_commits/` has as its target an **SQS FIFO queue** `vlc-housing-silver.fifo`
            with a constant `MessageGroupId` (`silver`) and content-based deduplication. The
            message body is the event; the consumer ignores it.
        2. A **Lambda consumer** with `ReservedConcurrentExecutions = 1`, an event source mapping
            with `BatchSize = 10` and a batching window of 60 seconds, a 15-minute timeout, and a
            queue visibility timeout of 16 minutes. It calls `ecs:RunTask` (Fargate, the task
            definition, `--mode incremental`), polls `ecs:DescribeTasks` until the task stops, and
            returns normally on exit code 0 or raises otherwise. A raise redelivers the batch; the
            queue's redrive policy moves it to a **dead-letter queue** after three receives.
            **The message is a wake-up call and nothing more.** Its body is never read, because
            the task finds its work by the set difference, and a message that encoded "process
            run X" would be a second source of truth that drifts from the ingest log on a lost
            event, a duplicate delivery or a catch-up. The dead-letter queue therefore holds no
            information worth replaying: it exists to **stop the retry loop** after three
            consecutive failures, which mean a defect rather than bad luck, and to raise an alarm.
            Its messages are deleted once the cause is fixed; the next commit or the catch-up finds
            the same pending runs. A defective run blocks every later run on purpose: `listing`
            and `listing_text` are only correct when every run is in, so skipping one would be the
            silent error this feature exists to prevent.
        3. **Why it serializes:** a FIFO queue delivers the messages of one message group in order
            and to at most one consumer at a time, and the consumer's concurrency is one. Commits
            that arrive while a task runs wait in the queue and start the next task afterwards;
            that task finds them all pending and processes them together. No lease, no time
            window, no self-check in the task.
        4. Silver writes under `silver/`, so it cannot trigger itself.
    - **Catch-up:** a daily EventBridge Scheduler entry sends one message to the same queue.
        One path for every start. **Metric:** `PendingRuns` in `vlc-housing-silver` after every
        run; an alarm on `> 0` for six hours. **Dead-letter alarm:** on
        `ApproximateNumberOfMessagesVisible > 0` of the dead-letter queue.
    - **Alerts:** a rule on the Silver cluster's `STOPPED` tasks with a non-zero exit or a stop
        code other than `EssentialContainerExited` publishes to an SNS topic with an email
        subscription (`var.alert_email`). **Logs:** 30 days; counts, days, run ids and snapshot
        ids only.
- **OOP / SOLID:** pure functions for the pending set, the union of days, the runs per day, the
    affected listings and the commit checks; the injected boundaries are the S3 client (listing
    and reading commits), the DuckDB connection, the Iceberg catalog and the clock. A frozen value
    object for a parsed commit. No new class otherwise.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - A checkpoint outside Iceberg (DynamoDB, a file): not atomic with the data (ADR 0002/0003).
    - Reading landing's whole detail history nightly: Silver `listing_detail` holds it.
    - Iceberg `MERGE` through Spark or Athena: PyIceberg's `upsert` suffices at this size.
    - Month partitions: they need a native dependency (ADR 0002).
    - Step Functions and a NAT gateway: as in ADR 0003 and FEATURE-003.
    - Serialization inside Silver (an S3 lease, an `ecs:ListTasks` guard, or a re-verify loop):
        each needs a second mechanism for its own race or a documented residual gap. The queue
        makes "never two tasks" a property of a managed service.
    - Step Functions with a named execution per time window: it serializes only within the
        window and needs a guard at the boundaries. Rejected by the user on 2026-10-19.
    - Keeping the v1 reading path beside v3: two code paths for one rule set.

## Approach

### Phase 1 — contract, synthetic v3, and the pending set

- [ ] **1.1 Pin landing v3.** RED: a test asserts the SHA-256 of `contracts/landing/v3/schema.json`
    equals the digest above and that every table's columns are v1's plus `run_id` after `source`;
    `src/contracts.py` gains `landing_v3_columns()` and `landing_v3_sha256()`. Fails because the
    functions do not exist. GREEN: the two functions. v1's pin stays.
- [ ] **1.2 The synthetic writer and generator write landing v3.** RED:
    `write_landing_tables(directory, rows_by_table)` keeps its row-based interface, requires
    `run_id` on every row, writes `landing/v3/<table>/run_date=…/run=…/part-0.parquet` per run
    and `_commits/<run_id>.json` with the contract's fields true of the files;
    `generate_landing(directory, seed)` assigns its rows to runs by their instants and adds
    scenarios: a day touched by two live runs, a replay run re-deriving an old day with a newer
    `ingested_at`, an import run (`import-<date>`) with records and no reports, a `failed` run, a
    run with no rows for one table (a zero-row file), and a listing whose chosen detail record
    is in an earlier run than its latest observation. Fails because the writer produces v1
    files. GREEN: the v3 writer and the scenarios.
- [ ] **1.2b FEATURE-001's rule tests move to v3 fixtures.** Validation-first migration, no new
    behaviour: `test_silver_walk`, `_observation`, `_listing`, `_listing_text`, `_redaction` and
    the boundary tests build their rows with the v3 writer (adding `run_id`) and read through the
    v3 reader of task 2.1; every assertion stays. The suite's test count does not fall. Ordered
    after 2.1, which it depends on.
- [ ] **1.3 Commits, the pending set, and the runs per day (pure).** RED, over hand-written
    commit dicts and an ingest-log mapping: pending = commits − ingested, oldest first by the
    date in the id; `days_of(pending)` per table; `runs_for(days, commits ∪ ingested)` per table
    and day; a malformed commit (missing field, wrong contract or version, wrong digest) raises
    `LandingContractError`. GREEN: `src/silver/commits.py`.

### Phase 2 — the per-day tables

- [ ] **2.1 Landing v3 is read and checked per run.** RED: `register_landing_v3(connection,
    location, runs_by_table, commits)` registers `landing_<table>` views over exactly the named
    runs' files per table (local directory or `s3://`), after checking columns, the commit's
    `row_counts`, nulls, uniqueness within a run and pairing within a run; a file whose row count
    differs from its commit raises; `listing_text` may be asked for a different run set than the
    other tables (task 3.2 reads the chosen records' runs). Fails because the function does not
    exist. GREEN: the v3 reader. The v1 `register_landing` is removed only after 1.2b has moved
    its boundary tests.
- [ ] **2.2 The newest sighting rule.** RED: two runs of one day with the same listing in one
    area → `observation` keeps the later run's row; a replay run re-deriving a detail with the
    same `observed_at` and a newer `ingested_at` → `listing_detail` and `listing_text` keep the
    replay's row; FEATURE-001's cross-area cases unchanged. GREEN: a `newest_sighting` view before
    the existing statements; `listing_detail.sql` as a Silver table.
- [ ] **2.3 Partitioned tables and day replacement in the sink.** RED: `walk`, `observation` and
    `listing_detail` are created partitioned by `identity(observed_date)`; `replace_days(table,
    rows, days)` overwrites exactly those days and leaves the others; an existing unpartitioned
    table is evolved with `update_spec()`; snapshot properties carry `landing_run_ids`. GREEN:
    the sink.
- [ ] **2.4 The ingest log.** RED: `landing_ingest` is created with the contract's commit
    fields; `ingested_runs(catalog)` returns run id → observed dates and `run_started_at`;
    `record_ingested(catalog, commits)` upserts on `run_id`, so recording the same commits twice
    leaves one row per run; a run whose process fails before the log leaves the log unchanged.
    GREEN: `src/silver/ingest.py`.

### Phase 3 — the history-wide tables and the equality

- [ ] **3.1 Affected listings.** RED: the ids in Silver's previous rows of the replaced days and in
    the new rows, union; a listing present yesterday in a replaced day and absent today is in the
    set. GREEN: a pure function over two Arrow tables.
- [ ] **3.2 `listing` and `listing_text` for affected ids.** RED: `listing` recomputed from
    Silver `observation` and Silver `listing_detail` filtered to the affected ids with
    FEATURE-001's rules plus `run_id` of the chosen record; `listing_text` read from landing by
    the chosen records' distinct `run_id`s (the run's `run_date` from the ingest log), joined on
    `(run_id, area, source_listing_id, observed_at)`, redacted `r1`; both upserted on
    `(source, source_listing_id)`, and a listing with no rows left deleted from both; an
    unaffected listing's row is byte-identical afterwards; the scenario whose chosen record sits in
    an earlier, already ingested run gets that run's text. GREEN: the two statements over Silver
    plus the targeted landing read, and the upsert and delete in the sink.
- [ ] **3.3 Incremental equals backfill.** RED: over the synthetic v3, processing commits one at a
    time in id order, then three at a time, then all at once (`--mode backfill`), gives the same
    row set per Silver table and the same ingest log; a run interrupted after the per-day commits
    and repeated gives the same result. GREEN: whatever the three runs disagree on.

### Phase 4 — the command

- [ ] **4.1 Modes and locations.** RED: `--mode incremental` with nothing pending exits 0 and
    prints "nothing pending"; `--mode backfill` processes everything; `--landing s3://…` lists and
    reads commits through an injected S3 client and registers files by their `s3://` paths; a
    `PendingRuns` count is printed and, with `--metrics`, published through an injected
    CloudWatch client; exit 1 on `LandingContractError` or `SilverSchemaError` before any commit.
    GREEN: the CLI.
- [ ] **4.2 The queue consumer.** RED, with fake ECS and SQS clients in
    `tests/test_silver_consumer.py`: `handler(event, context)` in `src/silver/consumer.py` starts
    exactly one task per invocation whatever the batch size, polls until the task stops, returns
    normally on exit code 0, raises on a non-zero exit code or a task that never started (so the
    batch is redelivered), and raises before the Lambda timeout when the task is still running
    (so the message is not silently dropped). The handler imports boto3 only; it never imports
    DuckDB or PyIceberg, because it is packaged as a plain zip. GREEN: the handler.

### Phase 5 — the image and the infrastructure

- [ ] **5.1 Dockerfile, `.dockerignore`, and the image workflow.** Validation-first: a local
    `linux/arm64` build runs `--landing /tmp/landing/v3 --catalog local --mode backfill` over the
    synthetic landing and exits 0; the workflow builds on every pull request without pushing and
    pushes to GHCR on `main`, tagged with the SHA.
- [ ] **5.2 Terraform: cluster, task definition, roles, logs, security group**
    (`infra/silver/ecs.tf`). Validation-first: `fmt`, `validate`, and a plan with placeholder
    variables listing exactly the expected resources.
- [ ] **5.3 Terraform: the queue, the consumer, the trigger, the catch-up, the alarms and the
    failure mail** (`infra/silver/queue.tf`, `trigger.tf`, `alerts.tf`; `trigger_enabled`,
    `catch_up_enabled`, default false). The FIFO queue with content-based deduplication and a
    16-minute visibility timeout; its dead-letter FIFO queue with `maxReceiveCount = 3`; the
    Lambda from `src/silver/consumer.py` zipped by Terraform's `archive_file`, with reserved
    concurrency one, a 15-minute timeout and an event source mapping (`batch_size = 10`,
    `maximum_batching_window_in_seconds = 60`); the EventBridge rule targeting the queue with
    `sqs_target.message_group_id = "silver"`; the Scheduler entry sending one message to the
    queue; alarms on the dead-letter queue depth and on `PendingRuns`; the stopped-task mail
    rule. Validation-first: `fmt`, `validate`, a plan listing exactly the expected resources,
    and `aws events test-event-pattern` with a synthesised commit event (matches) and a Silver
    data file event (does not match).

### Phase 6 — operation and documentation

- [ ] **6.1 First runs in AWS** (operator, commands prepared): apply with the starters off; one
    manual `run-task --mode backfill`; check every Silver table's row count against a local
    backfill of the same landing and that the ingest log holds every commit; enable the starters;
    after the next collection run is landed, check that its commit's event reached the queue,
    that the consumer started one Silver task, that the task processed exactly that run, and that
    `PendingRuns` reads 0; then send two messages within a minute and check that one task ran,
    not two. Record counts only.
- [ ] **6.2 Runbook, README and contract notes.** `docs/silver-rebuild.md` becomes the v3
    runbook (modes, the ingest log, the queue and why it serializes, why the message body is
    never read, the catch-up, the alarms, the dead-letter procedure — read the failed task's log,
    fix the cause, purge the dead-letter queue, never redrive it, and let the next start catch up
    — disabling the consumer's event source mapping before a manual backfill and re-enabling it
    after, pinning snapshots, rolling back, the equality check); the README and `CLAUDE.md` say
    Silver follows landing v3; FEATURE-001's note says its v1 path is retired; the registry.

## Files

- **Create:** `src/silver/commits.py`, `src/silver/ingest.py`, `src/silver/consumer.py`,
    `src/silver/sql/newest_sighting.sql`, `src/silver/sql/listing_detail.sql`, `Dockerfile`,
    `.dockerignore`, `.github/workflows/image.yml`, `infra/silver/ecs.tf`,
    `infra/silver/queue.tf`, `infra/silver/trigger.tf`, `infra/silver/alerts.tf`,
    `docs/silver-incremental.md` (or the rewritten runbook), tests per task including
    `tests/test_silver_consumer.py`.
- **Change:** `src/contracts.py`, `src/synthetic/landing.py`, `src/silver/landing.py` (v3 reader
    replaces v1), `src/silver/rebuild.py` (modes, locations, the run loop), `src/silver/sink.py`
    (partitions, day replacement, upsert), `src/silver/sql/{observation,listing,listing_text}.sql`,
    `infra/silver/variables.tf`, `infra/silver/main.tf` (database description), `README.md`,
    `CLAUDE.md`, `docs/silver-rebuild.md`, `dev/plans/README.md`, `dev/plans/FEATURE-001-silver-rebuild.md`.
- **Remove:** the v1 reading path in `src/silver/landing.py` and the v1 file layout of the
    synthetic writer, after their tests have been migrated (1.2b). `contracts/landing/v1/` stays,
    pinned.

## Test strategy

- **Unit:** the pending set, days and runs per day, commit validation, the newest-sighting rule
    per table (including the `import-<date>` ordering case), affected listings, the ingest log
    round trip and its idempotent upsert, the sink's day replacement, upsert and delete against a
    SQLite catalog in a temporary directory, the queue consumer against fake ECS and SQS clients.
- **Migrated coverage:** every FEATURE-001 rule test runs on v3 fixtures; the total test count
    never falls below 81.
- **Contract / fixtures:** the v3 digest pin; every synthetic file and commit conforms; a
    commit with a wrong digest, a missing field or a row count that disagrees with its file is
    refused with counts only.
- **Integration (local):** the CLI over the synthetic v3 with a SQLite catalog, both modes;
    `--landing s3://` against a fake S3 client holding the synthetic files.
- **Equality and idempotency:** task 3.3; a processed run processed again changes nothing; two
    sequential runs over the same pending set leave one ingest row per run (upsert). Overlap is
    not tested in code, because the queue prevents it; 6.1 checks it in AWS.
- **Configured checks:** unit tests, the leak guard, workflow consistency, ruff lint and format,
    `terraform fmt -check` and `validate` under `infra/silver/`.
- **Manual:** the local image run (5.1), the plan (5.2, 5.3), the first runs in AWS (6.1).

## Operational and compliance impact

- **Data sourcing / legal:** Silver reads landing v3 only, from the private bucket, as the
    operator. Nothing here fetches anything from a portal.
- **Privacy / security:** descriptions enter Silver redacted (`r1`) as before. The task role
    reaches `landing/v3/` (read) and `silver/` (read, write) and the Glue database, nothing else.
    The image is public and holds code and contracts only. No credential exists in this
    repository.
- **Observability:** the task logs pending, processed and skipped runs, days per table, affected
    listing counts and snapshot ids; `PendingRuns` with an alarm; failures mailed.
- **Failure and recovery:** every run is a pure function of immutable landing plus a set
    difference; a failed task raises in the consumer, the batch is redelivered up to three
    times, then it is dead-lettered with an alarm. The dead-letter queue is a circuit breaker,
    not a buffer: its messages are purged after the fix and never redriven, because the next
    commit or the catch-up finds the same pending runs. A contract failure names the landing run
    at fault, and the fix happens in the acquisition repository; Silver never skips a run. A
    backfill repairs anything. A manual backfill runs only with the consumer's event
    source mapping disabled (runbook).
- **Migration / compatibility:** the first run is a backfill that evolves the existing tables'
    partition spec; snapshot ids pinned before it stay readable. The v1 landing path disappears
    from this repository; the acquisition repository keeps rebuilding v1 only on demand.

## Estimated monthly cloud cost

| Component | Pricing basis (eu-central-1) | Assumption | Est. / month |
|---|---|---|---|
| Fargate ARM64 (1 vCPU, 2 GB) | ≈ $0.0454 per task-hour | ≈ 9 runs/day × 1–2 min | ~$0.30 |
| Public IPv4 | $0.005 per hour | ≈ 7 h | ~$0.04 |
| S3 GET/LIST (landing reads) and PUT (Silver files) | $0.00043 / $0.0054 per 1,000 | a few hundred a night | ~$0.02 |
| SQS FIFO (queue + dead-letter) | $0.50 per million requests, first million free | ≈ 1,500 requests/month | $0.00 |
| Lambda consumer | 1M requests and 400,000 GB-s free | ≈ 270 invocations × ≤ 2 min × 128 MB ≈ 4,000 GB-s | $0.00 |
| Glue Data Catalog | free tier (1M objects, 1M requests) | 6 tables, hundreds of requests | $0.00 |
| CloudWatch (1 metric, 2 alarms, logs) | first 10 alarms free | | ~$0.01 |
| SNS email | free tier | | $0.00 |
| **Total (new AWS components)** | | | **~$0.37/month** |

- **Cost drivers & cheaper alternatives:** Fargate minutes. The cheaper alternative is the laptop
    run at $0, losing the automation. A NAT gateway (≈ $35/month) is avoided by the public IP.
- **External / non-AWS costs:** GHCR storage for a public image, free.
- **Budget check:** yes.

## Success criteria

- [ ] The v3 contract is pinned by digest; the synthetic landing writes v3 with true commits.
- [ ] Pending runs are a set difference between commits and the ingest log; a run processed
    twice changes nothing; a run interrupted before the ingest log is repeated harmlessly.
- [ ] Incremental processing through any sequence of commits equals a backfill, per table.
- [ ] Two live runs of one day and a replay of an old day yield the rows v1 kept, by the
    newest-sighting rule, covered by tests and by the synthetic scenarios.
- [ ] In AWS, a landing commit's event is queued and starts one Silver run that processes exactly
    that run; two messages within a minute start one task, not two; the catch-up, the
    dead-letter alarm and the `PendingRuns` alarm exist; failures are mailed; `PendingRuns`
    reads 0 after the first event-started run.
- [ ] No FEATURE-001 rule test is lost: the suite holds at least 81 tests after the migration.
- [ ] Every Silver snapshot names the landing runs that built it.
- [ ] Nothing in this repository reads landing v1 any more.
- [ ] Every behaviour test-first; unit tests, leak guard, workflow consistency, lint, format and
    Terraform checks green.

## Open questions & risks

- **Risk:** the first backfill evolves the partition spec of tables that already hold a snapshot.
    *Mitigation:* `update_spec()` is metadata-only in Iceberg; the backfill rewrites every row
    anyway; a pinned pre-feature snapshot stays readable.
- **Risk:** a burst of landing commits (a landing catch-up or `--all`) queues many messages.
    *Mitigation:* the consumer takes them in batches of ten; each task processes everything
    pending, so a burst of N commits costs at most a few tasks, never N, and never two at once.
- **Risk:** a Silver task outlives the consumer's 15-minute timeout (Assumption 7). *Mitigation:*
    the consumer raises before its own timeout, the batch is redelivered, the next task repeats
    the pending set; after three receives the dead-letter alarm fires. A backfill does not go
    through the queue.
- **Risk:** an operator redrives the dead-letter queue out of habit. *Mitigation:* harmless but
    pointless — it starts one more task that finds the same pending set; the runbook says to
    purge instead, and the alarm clears either way.
- **Risk:** an operator starts a manual run while the consumer is active. *Mitigation:* the
    runbook's first step for any manual run is disabling the event source mapping; 6.2 documents
    it, and the `PendingRuns` alarm covers a forgotten re-enable within six hours.
- **Risk:** the `landing_ingest` table grows one row per collection run forever. *Mitigation:*
    about 3,000 rows a year; one scan per run.
- **Question (operator, before 6.1):** keep the catch-up at 05:30 UTC, after landing's own
    catch-up at 05:00? Default: yes.

## Progress log

- **2026-10-19** — Revised after the review of 2026-10-18 (Changes Recommended). H1: runs are
    serialized by an SQS FIFO queue with one message group and a single-concurrency Lambda
    consumer that runs the Fargate task and waits for it; the user chose this over Step Functions
    (a named execution per time window still needs a boundary guard) and over an S3 lease (a
    second mechanism inside Silver). M1: the ingest log is upserted by `run_id` and Assumption 5
    now states that PyIceberg rebases conflicting commits. M2: `listing_text` is read from the
    chosen record's own landing run by `run_id`. M3: a migration task keeps every FEATURE-001 rule
    test; the estimate rises to about 28 h. L1–L4 adopted: pre-installed DuckDB extensions,
    bounded snapshot properties, ordering by `run_started_at`, the commit's `is_replay` kept for
    audit only. Clarified on the user's question: the queue message is a wake-up call whose body
    is never read, and the dead-letter queue is a circuit breaker with an alarm, never a buffer
    to redrive (Assumption 8).
- **2026-10-18** — Plan written against landing v3 and ADR 0003, superseding FEATURE-003. The
    Silver-side design of FEATURE-003 (day replacement, affected listings, the task and its roles)
    carries over; the manifest checkpoint becomes an Iceberg ingest log and a set difference; the
    trigger is the commit's own event; a newest-sighting rule replaces the v1 assumption of one
    row per listing-day per area.
