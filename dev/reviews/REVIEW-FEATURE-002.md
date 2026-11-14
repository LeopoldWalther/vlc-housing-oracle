# Review — FEATURE-002: Listing events: disappeared and reappeared

**Reviewer:** `reviewer` · **Date:** 2026-11-14 · **Plan:** [FEATURE-002](../plans/FEATURE-002-listing-event.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible and small: one statement over Silver's own tables and one whole-table
write. The rule reproduces on the synthetic landing and on real Silver with the counts the plan
states. Three corrections were needed and all follow from the repository: the task order would
have left the suite red for a whole phase, the whole-table write needed to be named as the
exception it is, and the first real run can be verified without a local copy of real landing.
They are folded into the plan. The next step is implementation.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-002-listing-event.md` (🔵 Planned, registered);
    FEATURE-004 🟢 Complete. The plan and its registry row are uncommitted on `main`.
- **Code, tests, data, and configuration:**
    - `src/silver/rebuild.py`: `SILVER_TABLES` drives `build_tables`; `process_landing` writes the
        day tables, then `listing` and `listing_text`, then the ingest log. `_compute_listings`
        replaces the temporary table `silver_observation` with the affected listings' rows.
    - `tests/test_silver_rebuild.py:62` and `:89` assert
        `set(SILVER_TABLES) == set(result.snapshot_ids)`; `:66` asserts that every table has at
        least one row on the synthetic landing. `tests/test_silver_incremental.py:72` compares
        every table of `SILVER_TABLES` between a backfill, stepwise runs and the oracle from
        `build_tables`.
    - `src/silver/sink.py`: `_table_for` creates a missing table from the Arrow schema in either
        mode; `write_silver_tables` overwrites a table whole and returns the snapshot id.
    - `infra/silver/ecs.tf`: the task role may create and update any table of the Silver
        database and read and write under the Silver prefix. No infrastructure change is needed.
    - `contracts/landing/v1/README.md`: a replay is never evidence of disappearance; an empty
        card does not make a listing part of an area's stock; `run_ended_at` exists to tell
        whether a walk lies inside one UTC day. The rule uses all three as the contract intends.
    - `.github/workflows/ci.yml` runs the commands the plan names.
- **Evidence reviewed:**
    - **The rule on synthetic landing, reproduced:** the plan's statement, run over
        `build_tables` of `generate_landing_v3(seed=1)`, returns 39 `disappeared` and 17
        `reappeared` rows with a unique key. The walk of the run that crosses midnight is
        excluded as evidence, and the listing seen in two runs of one day gets no event on that
        day. This proves the statement runs in DuckDB 1.5.6 and that the existing "every table
        has rows" assertion will hold; it is not a test.
    - **The rule on real Silver, counts only:** the architect's Athena run of the same rule
        (8,056 and 396 events on 2026-11-14) was made in this session and is taken as stated; the
        supporting facts (0 of 918 complete live walks cross midnight; 45 of 21,850 listings with
        a real card in more than one area) come from the same session.
    - **Cost of reading Silver whole, measured by the reviewer:** a PyIceberg scan of the six
        needed `observation` columns (105,521 rows, 24 data files) took 1.7 s from a laptop, and
        `walk` (2,382 rows) 1.0 s. The plan's 1.1 s covered only the computation. Together a run
        gains a few seconds today. The number of data files grows with the days collected, so
        the read grows roughly linearly; nothing suggests more than tens of seconds within two
        years.
- **Unverified assumptions:** the read time at two years of volume is extrapolated, not
    measured. Owner: operator; verification point: the task duration recorded in task 3.2 and
    visible in every task log afterwards.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | One table with a stated key; exclusions are explicit. |
| Repository and dependency assumptions match reality | Pass after correction | M1: adding the table to `SILVER_TABLES` in phase 1 breaks two existing test files. |
| Supporting evidence is reproducible and sufficient | Pass | Rule reproduced on synthetic data; read time measured on real Silver. |
| Design is the simplest adequate option | Pass | Whole-table recomputation removes the affected-set logic an upsert would need; M2 records why it is allowed. |
| TDD slices and validation commands are executable | Pass after correction | M1: every task now ends with a green suite, and no test is written after the code that satisfies it. |
| Data integrity, data sourcing, privacy, and security are addressed | Pass after correction | M3: verification without a local copy of real landing. Provenance, censoring and retraction are stated. |
| Operations, recovery, migration, and cost are addressed | Pass | Written before the ingest log; additive; $0. |

## Strengths

- The table is a pure function of two Silver tables, so order independence and idempotency are
    properties of the design, not of extra code.
- "Nobody looked" yields no event. The rule cannot turn a collection stall into disappearances.
- Facts stay facts: single-miss returns are recorded, not smoothed away, and the plan measures
    how many there are.
- The plan measured its own weakest assumption (one area per listing) instead of asserting it.

## Findings

### 🟡 M1 — Phase 1 would leave the suite red, and one task had no failing test

- **Problem:**
    - Task 1.1 appended `listing_event` to `SILVER_TABLES`. From that commit until the pipeline
        task, `tests/test_silver_rebuild.py` and `tests/test_silver_incremental.py` fail, because
        `process_landing` does not write the table yet. Four task branches could not pass their
        own validation.
    - Task 2.2 (retraction) was expected to pass without new code after 2.1. A test written
        after the code that satisfies it never fails first.
- **Evidence:** `tests/test_silver_rebuild.py:62`, `:89`; `tests/test_silver_incremental.py:72`
    and `:93`; the plan's own wording "expected to pass with 2.1".
- **Impact:** the Implementer would have to weaken a check or invent an order.
- **Recommendation (applied):**
    - Phase 1 tests the rule through one public function, `listing_events(connection, walk,
        observation)`, on hand-written Silver rows. That is also the narrower and faster test.
    - `SILVER_TABLES` gains the table in the pipeline task, whose RED step also holds the
        retraction test and a new test for tables that predate the feature.
    - The phase has two tasks instead of three.
- **Disposition required:** fixed in the feature plan and in the technical plan.

### 🟡 M2 — A whole-table write in every run needs to be an explicit exception

- **Problem:** `CLAUDE.md` and ADR 0003 say Silver replaces day partitions and that a full
    rebuild is a deliberate backfill. The plan rewrites one table whole in every run and justified
    it by cost alone, measured for the computation only.
- **Evidence:** `CLAUDE.md`, "Data and Lakehouse Rules"; ADR 0003, decision 6; the reviewer's
    read-time measurement above.
- **Impact:** without the reason on record, a later reader either "fixes" it into an upsert that
    reintroduces order dependence, or copies it for a table where it is not justified.
- **Recommendation (applied):** the plan's Design now says why: an event's day is decided by
    rows of other days, so no run owns a partition of this table; the write is still a
    replacement of a whole unit and reads Silver, not landing. The runbook task documents it, and
    task 3.2 records the task duration, which is the signal for switching to the fallback.
- **Disposition required:** accepted with rationale; fixed in the plan and in tasks 3.1 and 3.2.

### 🟡 M3 — The first real run was verified against a local copy of real landing

- **Problem:** task 3.2 compared the table with "a local incremental run on a sync of the same
    landing". That copies real landing to a laptop for a check that an independent query can do
    inside the lake.
- **Evidence:** the architect already ran the rule as Athena SQL on real Silver, with counts
    only. The session before deleted its local copies of real data on purpose.
- **Impact:** an avoidable copy of real data, and a weaker check: the local run would execute
    the same code and could only show that AWS and a laptop agree.
- **Recommendation (applied):** compare the table's counts per event type with the rule written
    in Athena SQL over the `walk` and `observation` snapshots of the same run. It is a second
    implementation and moves no data. The query is run privately and returns counts.
- **Disposition required:** fixed in the feature plan, its success criterion and task 3.2.

### 🟢 L1 — The two decisions the plan left to the user

- **Evidence:** the plan asked whether the day or the collection run is the unit, and whether a
    disappearance needs a second walk to count. The measurements support the plan's choices: no
    complete live walk crosses midnight, an area is walked twice on one day in about a tenth of
    the area-days, and a confirmation threshold would hide 259 of 396 returns.
- **Suggestion:** proceed with the day and without a threshold. Both stay the user's to reverse
    before task 1.1 starts; reversing the first one is a new plan, not a correction.
- **Benefit:** the plan is not held up by questions whose evidence points one way.

### 🟢 L2 — Compare rows as Arrow, not through `fetchall`

- **Evidence:** in the repository's environment, DuckDB's `fetchall` on a column of instants
    fails because `pytz` is not installed; `to_arrow_table().to_pylist()` works, and the existing
    tests use it.
- **Suggestion:** the new tests read results as Arrow rows.
- **Benefit:** avoids a new dependency for a test convenience.

### 🟢 L3 — A later change of the rule rewrites every event at the next run

- **Evidence:** the table is recomputed whole, so a changed statement reaches all history with
    the first run after deployment, not with a backfill.
- **Suggestion:** when the rule changes, bump `transformation_version` as the plan says and run
    the backfill in the same operator session, so no snapshot holds new events beside old rows of
    other tables.
- **Benefit:** keeps "a rule change is deliberate" true for this table as well.

## Alternatives considered

- **Upsert of affected listings** — recompute only the listings of the areas a run walked.
    Trade-off: a smaller read against a second affected-set rule that must also cover late runs.
    Verdict: stick with the plan; switch when the task duration says so.
- **Events per collection run** — exact to three hours. Trade-off: needs a per-run sighting
    table. Verdict: not at a weekly sweep.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| `event_date` is read as the day a listing left | Medium | Medium | `last_seen_date` on every row; the runbook states the interval | consumers; documentation |
| A walk is marked complete but missed cards | Low | Low | Recorded as a disappearance and a return; nothing in Silver can tell | operator; the share of returns after one missed walk |
| The whole-table read grows with history | Certain, slow | Low | Fallback named; duration recorded in 3.2 and in every task log | operator; task duration |
| A listing alternates between two areas | Low (0.2%) | Low | Accepted | — |
| Snapshots accumulate without expiry | Certain | Low | Covered by the pending expiry decision | user |

## Effort check

- **Plan estimate:** M (~8h)
- **Reviewer estimate:** M (~8h) — confidence Medium
- **Why it differs / hidden complexity:** unchanged in total. The temporary table
    `silver_observation` is reused by `_compute_listings`, so the events function must replace
    it and run after the listings; the wait for the image build and the approval-gated operator
    run add elapsed time, not effort.

## Reuse & conflicts

- **Reuse:** `write_silver_tables` for the overwrite; `tests/test_silver_incremental.py` as the
    equality harness; the runbook's deployment steps.
- **Conflict / coordinate with:**
    - `dev/plans/README.md` is also changed by the FEATURE-007 plan; a one-line conflict for
        whichever merges later.
    - FEATURE-007's plan and review files lie uncommitted in the same working tree; the
        integration branch of this feature takes only FEATURE-002's artifacts.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-002-technical-plan.yaml`
- **Tasks:** 8. The graph is acyclic; the critical path is
    1.1 → 1.2 → 1.3 → 1.4 → 2.1 → 2.2 → 3.1 → 3.2.
- **TDD contract:** RED failures and commands verified yes. Phase 1 fails on the missing
    function, 2.1 on the missing table, 2.2 on the missing report line.
- **File boundaries:** create and modify paths lie inside the allowed files; none overlaps a
    forbidden path.
- **Git workflow:** an integration branch from `main` holds the plan, review and technical plan;
    tasks branch from it in order and merge back with `--no-ff`; one pull request to `main`;
    task 3.2 commits its note on its own branch from `main`.
- **Required checks:** `python-lint-and-test`, `workflow-consistency`.

## Approval criteria

- **Blockers (must fix):** none.
- **Required dispositions:** M1 fixed; M2 accepted with rationale and documented; M3 fixed.
- **Optional:** L1 adopted as the plan's decision; L2 and L3 recorded as notes in the technical
    plan.
- **Accepted residual risk:** events of listings that alternate between areas; growth of the
    whole-table read; the interval-censored meaning of `event_date`.
- **Technical plan:** emitted.

## Next step

Run `/implementer FEATURE-002`.
