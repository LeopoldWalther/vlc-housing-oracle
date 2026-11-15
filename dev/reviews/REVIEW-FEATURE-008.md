# Review — FEATURE-008: Listing quality flags: implausible values and misplaced coordinates

**Reviewer:** `reviewer` · **Date:** 2026-11-15 · **Plan:** [FEATURE-008](../plans/FEATURE-008-listing-quality.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible and small: one statement over Silver's own `listing` and one whole-table
write, on the path `listing_event` already uses. Three corrections were needed, and all follow
from the repository:

- two tasks of phase 1 had no test that fails first;
- the integer products of the rules overflow on extreme values and would fail the Silver run;
- the comparison of the first real run would have compared an exact median with an approximate one.

They are folded into the plan. The next step is implementation.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-008-listing-quality.md` (🔵 Planned, registered);
    FEATURE-002 and FEATURE-005 are 🟢 Complete. The plan and its registry row are uncommitted on
    `main`.
- **Code, tests, data, and configuration:**
    - `src/silver/rebuild.py`:
        - `SILVER_TABLES` is in dependency order and drives `build_tables`, which keeps each
            result as the temporary table `silver_<name>`. A statement placed after `listing`
            can read `silver_listing`.
        - `process_landing` writes the day tables, then `listing` and `listing_text`, then
            `listing_event` with `write_silver_tables`, then the ingest log.
        - `_silver_columns` scans chosen columns of a Silver table and falls back to this run's
            empty result when the table does not exist yet. It covers a catalog whose `listing`
            table has not been created.
        - `_prepare` sets the session to UTC, so `year(detail_observed_at)` is the UTC year.
    - `tests/test_silver_rebuild.py:62` and `:89` assert
        `set(SILVER_TABLES) == set(result.snapshot_ids)`; `:63` asserts that every table has at
        least one row on the synthetic landing, which holds because it has listings with a
        detail record. `tests/test_silver_incremental.py:72` compares every table of
        `SILVER_TABLES` between a backfill, stepwise runs and `build_tables`; `:143` is the
        pattern for a catalog that predates a table.
    - `contracts/landing/v3/schema.json`: `price_eur`, `prev_price_eur`, `sqm_built`,
        `sqm_usable`, `rooms`, `bathrooms` and `year_built` are `INTEGER`; `latitude` and
        `longitude` are `DOUBLE`. The plan's "all inputs are integers" holds.
    - `src/synthetic/landing.py`: `_detail` accepts attribute overrides. The three listings of
        `_one_property_two_listings` are the only ones with coordinates, all in one
        municipality, so a fourth about 15 km away leaves the median beside them.
    - `infra/silver/ecs.tf`: unchanged since FEATURE-002, whose first run created a new table
        with the existing role.
- **Evidence reviewed:**
    - **Rule counts on real Silver:** the architect's Athena query of 2026-11-15 was run in this
        session and is taken as stated (57 sale and 9 rent listings with a plausibility flag; 274
        and 53 with a location flag). It used an approximate median.
    - **Integer overflow, reproduced by the reviewer in DuckDB 1.5.6:**
        `15000 * 200000::INTEGER` and `2 * 1500000000::INTEGER` both fail with
        `Out of Range Error: Overflow in multiplication of INT32`; with one `BIGINT` operand
        the product is exact.
    - **Median and year:** `median` over four doubles returns the mean of the two middle
        values, and `year` of an instant in a UTC session returns the UTC year.
- **Unverified assumptions:** the task duration after the change is extrapolated from
    `listing_event`. Owner: operator; verification point: task 3.2.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | One table with a stated key and twelve named flags; exclusions are explicit. |
| Repository and dependency assumptions match reality | Pass | Paths, the write path and the fallback for a missing table exist as described. |
| Supporting evidence is reproducible and sufficient | Pass after correction | M3: the real-run comparison now uses the same median definition. |
| Design is the simplest adequate option | Pass | One statement and one write on an existing path; no new boundary. |
| TDD slices and validation commands are executable | Pass after correction | M1: every task of phase 1 now fails first. |
| Data integrity, data sourcing, privacy, and security are addressed | Pass after correction | M2: an extreme value is flagged and cannot fail the run. The table holds ids and booleans. |
| Operations, recovery, migration, and cost are addressed | Pass | Written before the ingest log; additive; $0. |

## Strengths

- The table is a pure function of `listing`, so "incremental equals backfill" holds by design.
- A flag is never null and is true only on evidence. Missing data cannot look like a broken rule.
- The year bound uses the observation, not a clock, so a backfill gives the same rows.
- The plan learned from FEATURE-002: `SILVER_TABLES` gains the table only in the pipeline task.

## Findings

### 🟡 M1 — Two tasks of phase 1 had no failing test

- **Problem:**
    - Task 1.1 implemented all ten plausibility flags, including `year_built_out_of_range`. Task
        1.2 then tested the year bound, which a correct 1.1 already satisfies.
    - Task 1.4 tested the row contract (one row per listing with a detail record, order,
        version). Any statement that passes 1.1 already needs that skeleton.
- **Evidence:** the plan's phase 1 as written; the same defect was finding M1 of
    `REVIEW-FEATURE-002.md`.
- **Impact:** a test written after the code that satisfies it never fails first.
- **Recommendation (applied):** phase 1 has three tasks, and each adds columns that do not exist
    before it:
    - 1.1: the row contract and the nine plausibility flags other than the year flag;
    - 1.2: the year flag;
    - 1.3: both location flags, and the empty-table contract.
- **Disposition required:** fixed in the feature plan and in the technical plan.

### 🟡 M2 — The integer products overflow on the values the rules exist for

- **Problem:** the plan compares `price_eur` with threshold × `sqm_built`, and
    `2 × price_eur` with `prev_price_eur`, on `INTEGER` columns. DuckDB raises an error on a
    32-bit overflow.
- **Evidence:** reproduced above. `15000 × sqm_built` overflows from a built area of 143,166 m²,
    and `2 × price_eur` from a price of 1,073,741,824.
- **Impact:** one listing with an absurd value fails the statement, the task exits non-zero, and
    its landing runs stay pending until someone intervenes. The rule that should flag the value
    would stop Silver instead.
- **Recommendation (applied):** every product is computed as `BIGINT`, as `pairs.sql` already
    does for the price gap. Task 1.1 tests a built area of 300,000 m² and a price of
    2,000,000,000.
- **Disposition required:** fixed in the feature plan and in task 1.1.

### 🟡 M3 — The first real run was compared across two median definitions

- **Problem:** the success criterion allowed "the difference is explained", because the
    architect's Athena query used `approx_percentile` and the table uses an exact median.
- **Evidence:** the plan's third open assumption.
- **Impact:** a small real difference could hide behind the expected one, so the comparison
    would not verify the far flag.
- **Recommendation (applied):** the Athena rule of task 3.2 computes the exact median: the mean of
    the two middle values of the sorted coordinates. The counts per flag must then be equal.
- **Disposition required:** fixed in the feature plan, its success criterion and task 3.2.

### 🟢 L1 — A threshold change reaches every row with the next run

- **Evidence:** the table is recomputed whole, so a changed statement rewrites all flags at the
    first run after deployment, not with a backfill.
- **Suggestion:** when a threshold changes, bump `transformation_version` and run the backfill
    in the same operator session, as for `listing_event`.
- **Benefit:** no snapshot set holds new flags beside old rows of other tables.

### 🟢 L2 — Every processed run writes a new snapshot of the table

- **Evidence:** the table is written whenever a run processes a commit, including commits
    without rows. About eight runs a day arrive.
- **Suggestion:** none now. Snapshot expiry is the pending decision that already covers
    `listing_event`.
- **Benefit:** records the growth so that it is not a surprise.

### 🟢 L3 — Almost every synthetic listing has `coordinates_missing`

- **Evidence:** only the three listings of `_one_property_two_listings` have coordinates.
- **Suggestion:** the test of task 2.2 asserts the specific flags on the two new listings and
    that no other listing has a plausibility flag or the far flag. It does not assert that
    `coordinates_missing` is rare.
- **Benefit:** the Implementer does not have to add coordinates to every synthetic listing.

## Alternatives considered

- **Flag columns on `listing`** — no seventh table. Trade-off: the far flag depends on other
    listings, so an upsert by affected listing would leave stale flags. Verdict: stick with the
    plan.
- **A Gold dataset** — the plan's first draft. Verdict: rejected by the user's layer decision of
    2026-11-15.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| A flag is read as "wrong" instead of "check" | Medium | Medium | The runbook states the reading and the house share | consumers; documentation |
| The centre of a small municipality moves between runs | Low | Low | One municipality below ten listings today; the table reflects current listings | operator; counts in 3.2 |
| A threshold change bumps the version of all Silver | Low | Low | Accepted as Silver's rule; L1 | user |
| Snapshots accumulate without expiry | Certain | Low | Covered by the pending expiry decision | user |
| Labels live in two layers until the property groups move | Certain | Low | Named as the next feature | user |

## Effort check

- **Plan estimate:** M (~7h)
- **Reviewer estimate:** M (~7h) — confidence Medium
- **Why it differs / hidden complexity:** unchanged in total. Phase 1 has one task fewer; the
    wait for the image build and the approval-gated deployment add elapsed time, not effort.

## Reuse & conflicts

- **Reuse:**
    - `write_silver_tables` and `_silver_columns` in `src/silver/rebuild.py`;
    - `tests/test_silver_incremental.py` as the equality harness and its pre-feature test as the
        pattern;
    - the deployment steps of FEATURE-002, task 3.2.
- **Conflict / coordinate with:** none. No other feature is in progress.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-008-technical-plan.yaml`
- **Tasks:** 8. The graph is acyclic; the critical path is
    1.1 → 1.2 → 1.3 → 2.1 → 2.2 → 2.3 → 3.1 → 3.2.
- **TDD contract:** RED failures and commands verified yes. 1.1 fails on the missing function,
    1.2 and 1.3 on missing columns, 2.1 on the missing table, 2.2 on the missing listings, 2.3 on
    the missing report line.
- **File boundaries:** create and modify paths lie inside the allowed files; none overlaps a
    forbidden path.
- **Git workflow:** an integration branch from `main` holds the plan, review and technical plan;
    tasks branch from it in order and merge back with `--no-ff`; one pull request to `main`;
    task 3.2 commits its note on its own branch from `main`.
- **Required checks:** `python-lint-and-test`, `workflow-consistency`.

## Approval criteria

- **Blockers (must fix):** none.
- **Required dispositions:** M1, M2 and M3 fixed.
- **Optional:** L1, L2 and L3 recorded as notes in the technical plan.
- **Accepted residual risk:** a moving centre in small municipalities; snapshot growth; labels in
    two layers until the property groups move.
- **Technical plan:** emitted.

## Next step

Run `/implementer FEATURE-008`.
