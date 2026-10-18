# Review — FEATURE-004: Incremental Silver from landing v3, run by AWS

**Reviewer:** `reviewer` · **Date:** 2026-10-18 · **Plan:** [FEATURE-004](../plans/FEATURE-004-incremental-silver-from-landing-v3.md)
**Verdict:** ⚠️ Changes Recommended

## Summary

The direction is sound and its foundations are verified: landing v3 is live, its contract is pinned
on both sides, the set difference over run ids is the right checkpoint, and the newest-sighting
rule restores what v1 kept.

**One data-integrity gap blocks approval.** Two Silver runs that overlap with *different* pending
sets both commit, because PyIceberg 0.12 retries a conflicting commit automatically. The second
`listing` upsert can then overwrite rows computed without the first run's days, silently breaking
the feature's core guarantee (incremental equals backfill). The fix needs a design choice, so it
returns to the Architect or the user. Three further corrections are bounded.

## Evidence checked

- **Plan and dependencies:**
    - FEATURE-004 (🔵), [ADR 0003](../../docs/adr/0003-immutable-bronze-and-landing-per-run.md),
        [`contracts/landing/v3/`](../../contracts/landing/v3/README.md) and the registry, all on
        `main` (merged PRs #3 and #4).
    - FEATURE-003 is ⚪ superseded; no review is owed for it.
    - The acquisition repository's FEATURE-016 is 🟢 Complete (PR #49).
- **Contract:**
    - `schema.json` SHA-256 `0ccda584…e61b` matches in both repositories.
    - Every v3 table is v1's columns plus `run_id` (`VARCHAR`) after `source`, checked
        programmatically.
- **Landing v3 in the bucket (metadata only):** 146 commits under `landing/v3/_commits/`. The
    acquisition progress log of 2026-10-18 records:
    - 144 runs landed by `--all`;
    - v3 equal to v1 row for row minus `run_id` (1,091 / 54,124 / 17,167 / 17,167);
    - the task role reading S3 through DuckDB `httpfs`, so the plan's Assumption 3 holds;
    - its image pre-installs `httpfs` and `aws`, and they load with `--network none`.
- **Spike, PyIceberg 0.12.0, SQLite catalog, 2026-10-18:**
    - Two handles loaded from the same state both `append` the same `run_id`. The second logs
        `Commit failed due to a concurrent update, retrying (1/4)` and **succeeds**, leaving
        **2 rows**. A later append after a re-read leaves 3. Iceberg's optimistic commit therefore
        does not make concurrent PyIceberg writers fail; it rebases them.
    - From the ADR 0002 spike, reproduced in the FEATURE-003 review:
        - a no-op `upsert` or `delete` creates no snapshot;
        - `set_properties` inside a transaction is atomic with the data, and unchanged after a
            failed transaction.
- **Repository:** `main` is clean.
    - FEATURE-001's tests (81) build their fixtures with the v1 writer `write_landing_tables`, and
        the boundary tests use `register_landing` over v1. The plan removes both.
- **Unverified assumptions:**
    - **Fargate pulls the public GHCR image (Assumption 4):** verified in task 6.1.
    - **Daily `PendingRuns` alarm behaviour:** only observable in AWS.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | Equality with backfill, pending as a set difference, exclusions explicit |
| Repository and dependency assumptions match reality | Pass | v3 live and pinned, FEATURE-016 complete, Assumption 3 proven by the acquisition side |
| Supporting evidence is reproducible and sufficient | **Fail** | Assumption 5 is falsified by the spike (H1, M1) |
| Design is the simplest adequate option | Pass | Set difference, ingest log, commit-event trigger, no orchestrator |
| TDD slices and validation commands are executable | Pass with corrections | M2 (text source), M3 (test migration) |
| Data integrity, data sourcing, privacy, and security are addressed | **Fail** | H1: concurrent runs can lose a run's effect on `listing` |
| Operations, recovery, migration, and cost are addressed | Pass | ~$0.37/month verified ($0.0454/task-hour = 1 vCPU + 2 GB ARM) |

## Strengths

- **Pending work is a set difference over immutable run ids.** Order is irrelevant, a repeat is
    harmless, and a missed trigger is caught up. This removes the watermark and lag that made
    landing v2 fragile.
- **The ingest log stores each run's `observed_dates`.** "Every run that touches day d" can then
    be computed without reading every old commit (Assumption 6).
- **The newest-sighting rule is defined per table with total orders.** It reproduces v1's merge
    semantics, which the acquisition side's equality proof supports (Assumption 2).
- **Commit row counts are checked against the files**, so a file replaced after its commit is
    caught before any Silver commit.
- **The trigger cannot fire itself** (`landing/v3/_commits/` versus `silver/`), and the catch-up
    plus `PendingRuns` alarm cover a missing trigger.

## Findings

### 🔴 H1 — Overlapping runs with different pending sets can silently lose a run's effect on `listing`

- **Problem:**
    - Each run reads Silver `observation` and `listing_detail` to recompute the affected listings,
        then upserts `listing` and `listing_text`.
    - Run A (pending {X}) and run B (pending {Y}) can overlap. If B reads Silver before A's day
        commits are visible, B's recompute of a listing affected by both X and Y lacks X's days.
    - B's upsert then commits after A's, because PyIceberg rebases it on retry instead of failing.
        `listing` stays wrong until a later run happens to touch that listing.
- **Evidence:**
    - The spike: a conflicting PyIceberg commit retries and succeeds.
    - The plan's Assumption 5 ("one loses ... `CommitFailedException`") does not hold.
    - **Overlap is realistic:**
        - landing writes commits one after another when it lands several runs, for example its
            daily catch-up or `--all` (144 commits in 3 min 22 s);
        - each commit's event starts a Silver task;
        - each task runs 1–2 minutes.
- **Impact:** a silent violation of the success criterion "incremental equals backfill", in a
    history-wide table that downstream features (FEATURE-002, Gold, models) treat as truth. No
    alarm fires, because both runs exit 0.
- **Recommendation:** serialize Silver runs and drain, as part of the design. Recommended
    mechanism, consistent with ADR 0003's conditional writes:
    - **A lease object `silver/_lease.json`,** created with `If-None-Match: *` and holding the task
        id and an expiry of run start + the task's maximum runtime.
    - **A task that cannot create the lease exits 0** with "another run is active". Its commits
        stay pending, so nothing is lost.
    - **The holder drains.** After writing the ingest log, it lists the commits again and repeats
        while any are pending, then deletes the lease.
    - **An expired lease is taken over** with a conditional write on its ETag (`If-Match`).
    - Tests use a fake S3 client honouring both preconditions.

    The alternative is lock-free: after writing the ingest log, re-read it and recompute `listing`
    and `listing_text` for the union of affected ids of every run ingested since this run started,
    looping until stable. It needs no new object, but is subtler to test and reason about.
- **Owner / verification:**
    - The Architect, or the user, chooses the mechanism.
    - Re-review checks a test in which two runs with disjoint pending sets touching one listing end
        equal to a backfill.

### 🟡 M1 — The ingest log can hold duplicate rows; Assumption 5 must be rewritten

- **Problem:** `record_ingested` appends. Two runs over the same pending set, for example
    EventBridge's at-least-once delivery or the catch-up meeting an event, both append, because
    PyIceberg retries the conflicting append.
- **Evidence:** the spike left 2 rows for one `run_id`, and 3 after another append.
- **Impact:**
    - `ingested_runs` as a mapping still dedupes, so processing stays correct.
    - The plan's test "two runs over the same pending set leave one ingest row per run" fails as
        written, and the log stops being a clean audit record.
- **Recommendation:**
    - write the ingest log with `upsert(join_cols=["run_id"])`, or dedupe on read and drop that
        test;
    - restate Assumption 5 as "concurrent PyIceberg commits are rebased and retried; correctness
        relies on idempotent writes and on H1's serialization, never on a commit failing".
- **Disposition required:** fix.

### 🟡 M2 — Where `listing_text` reads the chosen record's text is unspecified

- **Problem:** Silver keeps no text history. The chosen detail record of an affected listing can
    belong to an old run that is not among the runs registered for this night's days. Task 2.1
    registers views over "exactly the named runs' files", so the text row is not in them.
- **Evidence:**
    - the data flow line "text = the chosen record's landing listing_text row";
    - task 3.2 recomputes `listing` from Silver tables only.
- **Impact:** the Implementer must invent the read, or `listing_text` misses rows for listings
    whose chosen record is old.
- **Recommendation:** with `run_id` now on the chosen record, read
    `landing/v3/listing_text/run_date=<d>/run=<run_id>/part-0.parquet` for each distinct chosen
    `run_id`, taking `<d>` from the ingest log's `run_started_at`. Join on
    `(run_id, area, source_listing_id, observed_at)`. State it in tasks 2.1 and 3.2 and test it with
    a listing whose chosen record is from an earlier, already ingested run.
- **Disposition required:** fix.

### 🟡 M3 — Removing the v1 writer and reader "with their tests" deletes FEATURE-001's rule coverage

- **Problem:**
    - Tasks 1.2 and 2.1 remove `write_landing_tables` (v1) and `register_landing` (v1) with their
        tests.
    - FEATURE-001's rule tests (`test_silver_walk`, `_observation`, `_listing`, `_listing_text`,
        `_redaction`, `_landing`) all build fixtures with that writer.
- **Evidence:** the 81-test suite on `main`. `CLAUDE.md` says "do not delete coverage merely to make
    a test pass".
- **Impact:** without migration, the dedupe orders, the empty-card rule, redaction `r1` and the
    boundary checks lose their tests. With migration, there is about 3–4 hours of work the
    estimate does not show.
- **Recommendation:**
    - the writer becomes v3-aware and keeps its row-based interface: rows gain `run_id`, files go
        to the run layout, and a commit is generated;
    - every FEATURE-001 rule test is migrated to it, not removed;
    - the boundary tests move to `register_landing_v3`;
    - add a separate task for this, and raise the estimate to about 26 hours.
- **Disposition required:** fix.

### 🟢 L1 — Pre-install DuckDB's `httpfs` and `aws` extensions in the image

- **Evidence:** the acquisition image does this, and its progress log shows both load with
    `--network none`. The plan's Dockerfile installs `requirements.txt` only, so DuckDB would
    download extensions at every run.
- **Suggestion:** install both as the runtime user at build time; 5.1's local run uses
    `--network none` for the extension check.
- **Benefit:** no runtime dependency on DuckDB's extension host.

### 🟢 L2 — Keep `landing_run_ids` in snapshot summaries small

- **Evidence:** a backfill's summary would list every run id, 146 now and about 3,000 a year, in
    every snapshot of every table, and each metadata file carries all summaries.
- **Suggestion:** store `landing_run_count` and the smallest and largest run id in the summary;
    the ingest log holds the list.
- **Benefit:** bounded metadata growth with the same traceability.

### 🟢 L3 — Order "latest run" by `run_started_at`, not by the `run_id` string

- **Evidence:** import runs are named `import-<YYYYMMDD>`, which sorts after every
    `YYYYMMDDTHHMMSSZ` lexicographically.
- **Suggestion:** break the newest-sighting tie by the commit's `run_started_at`, from the ingest
    log or the commit, then by `run_id`.
- **Benefit:** "latest" means latest.

### 🟢 L4 — Do not use the commit's `is_replay` for logic

- **Evidence:** the acquisition log says markers before 2026-10-14 carry `is_replay = false` for
    replays, and that readers should use the rows.
- **Suggestion:** keep it in `landing_ingest` for audit only, and say so.
- **Benefit:** no silent dependence on a known-wrong field.

## Alternatives considered

- **Disable the commit trigger and run only on a schedule** (for example 30 minutes after each
    collection slot). Overlap is gone by construction and no lease is needed. Trade-off: it loses
    ADR 0003 decision 4 ("the trigger is the data") and adds latency. Verdict: use it only if the
    user prefers not to add a lease; it is the simplest safe fallback.
- **A Silver lock in DynamoDB.** Equivalent to the S3 lease, but it adds a service and IAM.
    Verdict: the S3 lease suffices.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| Overlapping runs lose a run's effect on `listing` (H1) | Med (bursts) | High (silent) | Lease and drain, plus a two-runs equality test | Architect; re-review |
| Lease never released after a crash | Low | Low | Expiry with takeover; `PendingRuns` alarm after 6 h | Implementer; alarm |
| Public GHCR pull fails | Low | Med | Pull-through cache fallback (plan) | Operator; first run |
| Test migration larger than estimated (M3) | Med | Low | Separate task, estimate raised | Implementer |

## Effort check

- **Plan estimate:** L (~22h)
- **Reviewer estimate:** L (~28h), confidence Med
- **Why it differs:** test migration for M3 (+3–4 h) and a lease with drain and its tests for H1
    (+2–3 h).

## Reuse & conflicts

- **Reuse:**
    - FEATURE-001's SQL rules, sink and boundary checks;
    - the acquisition repository's image pattern (extensions pre-installed) and its
        conditional-write pattern (`If-None-Match`) for the lease.
- **Conflict / coordinate with:** none open. The acquisition side's daily landing catch-up runs at
    05:00 UTC; Silver's catch-up at 05:30 relies on the lease when both produce work together.

## Technical-plan readiness

- **Artifact:** withheld.
- **Missing readiness condition:** H1's mechanism decided and written into the plan; M1–M3
    applied.

## Approval criteria

- **Blockers (must fix):** H1.
- **Required dispositions:** M1 (fix), M2 (fix), M3 (fix).
- **Optional:** L1–L4. Recommended to adopt; all are small.
- **Accepted residual risk:** none yet.
- **Technical plan:** withheld pending re-review.

## Next step

The user, or the Architect, decides H1's mechanism (S3 lease with drain, recommended; lock-free
re-verify; or schedule-only). Then the Architect revises FEATURE-004 with H1 and M1–M3, and the
plan returns as `/reviewer FEATURE-004`.

Record post-implementation learning separately under `dev/plans/implementations/`; do not rewrite
the review decision after implementation.
