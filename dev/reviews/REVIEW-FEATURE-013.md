# Review — FEATURE-013: Property groups move from Gold to Silver

**Reviewer:** `reviewer` · **Date:** 2026-11-16 · **Plan:** [FEATURE-013](../plans/FEATURE-013-property-groups-in-silver.md)
**Verdict:** ✅ Approved

## Summary

The move is feasible and cheap: the rule is three pure functions and one statement, and the run
already has a place where whole tables are recomputed from Silver and written before the ingest
log. Four corrections were needed, and all follow from the repository:

- the plan did not say whether the Gold code is moved, copied or deleted, and its file lists
    contradicted each other;
- one pipeline test cannot be written on the synthetic landing as described;
- the tests that enumerate Silver's tables were not in the plan, and one of them would fail;
- the comparison of the first real run would have read two different `listing` snapshots.

They are folded into the plan. The next step is implementation.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-013-property-groups-in-silver.md` (🔵 Planned,
    registered); FEATURE-007 and FEATURE-008 are 🟢 Complete. The plan, the plans of FEATURE-009 to
    FEATURE-012 and the registry rows are uncommitted on `main`.
- **Code, tests, data, and configuration:**
    - `src/gold/property_group.py`:
        - `duplicate_pairs`, `group_of` and `property_group_rows` are pure; only
            `property_group_rows` knows the two lineage columns.
        - `duplicate_pairs` executes `pairs.sql` directly on the connection, so the file's macro
            works; it never passes through `build_tables`.
        - The module imports `NAMESPACE` from `src.silver.rebuild`. A Silver module that
            `rebuild.py` imports must not import `rebuild.py`.
    - `src/silver/rebuild.py`:
        - `process_landing` writes `listing_quality` and `listing_event` in one
            `write_silver_tables` call after the listings and before `record_ingested`.
        - `SILVER_TABLES` drives `build_tables`, which runs one SQL file per name. The property
            groups are not one statement, so the table cannot join that tuple.
        - `listing_quality()` replaces the temporary table `silver_listing` with the quality
            columns only. `_silver_columns` falls back to that temporary table when the catalog
            has no `listing` table yet. A read of `floor` or `condition` after the quality rule
            would fail in that case.
        - `update_listings` returns early when no listing is affected, so `listing` gets no new
            snapshot in such a run, while the recomputed tables do.
    - `tests/test_silver_rebuild.py:62` and `:89` assert
        `set(SILVER_TABLES) == set(result.snapshot_ids)`. `tests/test_silver_incremental.py:72`
        compares the tables of `SILVER_TABLES` between a backfill, stepwise runs and
        `build_tables`; `:143` is the pattern for a catalog that predates a table.
    - `src/synthetic/landing.py:524`: both listings of the synthetic duplicate are observed at
        one instant in one area, so they arrive in one commit.
    - `tests/test_gold_property_pairs.py` and `tests/test_gold_property_groups.py` test the three
        functions; `tests/test_gold_property_group_build.py` tests the command, the file and the
        synthetic duplicate.
    - Mentions of the Gold dataset outside `dev/`: `README.md:11` and `:32`,
        `docs/gold-property-group.md`, `docs/data-quality/silver-quality-2026-11.md:375`.
        `CLAUDE.md` names no Gold dataset. `infra/`, `Dockerfile` and the workflows do not
        mention Gold; the image copies all of `src/`.
- **Evidence reviewed:**
    - Athena, counts only, Silver of 2026-11-16: 23,169 listings, 23,159 with a detail record.
        The equality join of the pair rule yields 133,139 candidate pairs before the price,
        distance and floor conditions.
    - Local spike, synthetic rows, not kept: 23,159 listings with 804,835 candidate pairs and
        87,521 pairs. The pair statement took 0.14 s and grouping and rows 0.29 s. This is six
        times the real candidate count. It proves the recompute is negligible on a laptop; it
        does not measure the task, which task 3.2 does.
- **Unverified assumptions:**
    - Nothing outside the repository reads the Gold object. Owner: the user. Verification point:
        the approval for deleting the object in task 3.2.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | Rule `pg1` unchanged; success is equality with the old code on one snapshot |
| Repository and dependency assumptions match reality | Pass | After M1 and M3 |
| Supporting evidence is reproducible and sufficient | Pass | Candidate count and spike above |
| Design is the simplest adequate option | Pass | Same write path as `listing_quality`; no new abstraction |
| TDD slices and validation commands are executable | Pass | After M1 and M2 |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | Pure function of `listing`; ids and labels only; no IAM change |
| Operations, recovery, migration, and cost are addressed | Pass | After M4; $0; rollback is the previous image and a dropped table |

## Strengths

- The table is a pure function of `listing` and replaced whole, so incremental runs equal a
    backfill without any state.
- The rule, its thresholds and its version are untouched; only the place changes.
- The Gold object stays until the comparison has passed.

## Findings

### 🟡 M1 — The plan did not settle what happens to the Gold code, and when

- **Problem:** Task 1.1 said "move the functions", the test list said "(moved)", and task 3.3
    deleted `src/gold/` and all three Gold tests after the first real run. The three readings
    (move, copy, delete later) lead to different code on `main`.
- **Evidence:** A move in 1.1 breaks `src.gold.property_group.build` and its test unless Gold
    imports the rule back from Silver with adapted columns. Task 3.2 already takes the reference
    from "the commit before the merge", so nothing needs the Gold code on `main` after the merge.
- **Impact:** The Implementer would have to invent the lifecycle, or adapt code that is about to
    be deleted, and a second code PR would follow for the deletion alone.
- **Recommendation:** Tasks 1.1 and 1.2 create the Silver module and its tests as adapted copies
    and leave Gold untouched. Task 2.3 deletes `src/gold/` and the three Gold tests once the
    pipeline test covers the synthetic duplicate. Task 3.1 moves the documentation. The final PR
    therefore holds no Gold code. Task 3.3 is dropped; deleting the object is the last step of
    task 3.2.
- **Disposition required:** fixed in the feature plan and in the technical plan.

### 🟡 M2 — One pipeline test cannot be written as described

- **Problem:** Task 2.1 asked for "a listing that joins a group in a later run changes the
    earlier member's row".
- **Evidence:** `src/synthetic/landing.py:524`: both members of the only synthetic group arrive
    in one commit. The plan does not list the generator as a changed file.
- **Impact:** Without this test, an implementation that upserts only the affected listings' rows
    would pass every other test.
- **Recommendation:** Test the replacement directly: process half of the commits, overwrite
    `listing_property_group` with every listing as its own group, process the rest, and expect
    the rows of a backfill. No generator change.
- **Disposition required:** fixed in the feature plan and in task 2.1.

### 🟡 M3 — Tests that enumerate Silver's tables were missing from the plan

- **Problem:** The plan listed neither `tests/test_silver_rebuild.py` nor a way for tests to
    name the new table.
- **Evidence:** `tests/test_silver_rebuild.py:62` and `:89` compare `set(SILVER_TABLES)` with
    the run's snapshot ids and fail as soon as the run reports one more table. The table cannot
    join `SILVER_TABLES`, because `build_tables` runs one SQL file per name. The oracle of
    `tests/test_silver_incremental.py` comes from `build_tables` and cannot hold the table.
- **Impact:** A red suite in task 2.1 outside the task's allowed files.
- **Recommendation:** A constant `PROPERTY_GROUP_TABLE` in `src/silver/property_group.py`. The
    tests enumerate `(*SILVER_TABLES, PROPERTY_GROUP_TABLE)`, and the oracle for the table is the
    rule applied to the oracle's `listing`. Also read `listing` from Silver once, with the union
    of the columns both rules need, before either rule runs: this removes the dependency on the
    order of the calls found in the evidence.
- **Disposition required:** fixed in the feature plan and in task 2.1.

### 🟡 M4 — The comparison of the first real run read two `listing` snapshots

- **Problem:** Task 3.2 built Gold first, then deployed, then compared with the first run.
- **Evidence:** A run that processes a landing commit with listings writes a new `listing`
    snapshot between the two. The Gold build also overwrites the private object, which is one
    more approved write than the comparison needs, or writes listing-level rows to the laptop.
- **Impact:** Differences that come from the data, not from the code, or no comparison at all.
- **Recommendation:** After the first run on the new image, run the old rule in memory: a work
    tree at the commit before the merge, a one-off script that is not committed, the current
    `listing` snapshot through the catalog, and the Silver table beside it. The script first
    checks that the table's current snapshot is newer than `listing`'s. It prints counts only
    and writes nothing. The stale Gold object is then deleted with its own approval.
- **Disposition required:** fixed in the feature plan and in task 3.2.

### 🟢 L1 — `pairs.sql` needs no rewrite

- **Evidence:** The plan inlines the distance "because `build_tables` wraps a statement and
    cannot hold a macro". `duplicate_pairs` executes the file directly; `build_tables` never
    sees it.
- **Suggestion:** Copy the statement and change only the relation name. Applied in task 1.1.
- **Benefit:** The tested rule stays identical, which is the claim the feature makes.

### 🟢 L2 — `CLAUDE.md` needs no change

- **Evidence:** It describes the Gold layer in general and names no dataset.
- **Suggestion:** Removed from the changed files.
- **Benefit:** A smaller diff in the file that steers every session.

### 🟢 L3 — Links in closed plans will point to a deleted document

- **Evidence:** `dev/plans/FEATURE-007-property-groups.md`, its implementation note and
    `dev/reviews/REVIEW-FEATURE-007.md` mention `docs/gold-property-group.md`.
- **Suggestion:** Leave them; they record the state of their day.
- **Benefit:** History stays unedited. The search for the old path in task 3.1 covers `src`,
    `tests`, `docs` and `README.md` only.

### 🟢 L4 — One more snapshot per run

- **Evidence:** Every processed run overwrites the table, as it does for `listing_quality` and
    `listing_event`.
- **Suggestion:** Covered by the pending decision on snapshot expiry; nothing to do here.
- **Benefit:** None beyond the record.

## Alternatives considered

- **Keep the Gold command and schedule it after Silver** — no code move, one more task and
    trigger. Trade-off: two writers and two layers for labels of one entity stay. Verdict:
    rejected by the layer decision of 2026-11-15.
- **Connected components in SQL, as one statement in `SILVER_TABLES`** — fits `build_tables`.
    Trade-off: a recursive query replaces a tested function of 20 lines. Verdict: stick with the
    plan.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| The Silver table differs from the old rule on real data | Low | Med | Task 3.2 compares before the Gold object is deleted | Operator; a non-zero difference count |
| A later feature reads `property_group_id` across runs | Med | Low | Documented as stable within one snapshot | Consumer; the runbook |
| The first run finds no pending commit and writes nothing | Low | Low | The table appears with the next commit; the nightly catch-up brings one | Operator; no report line |

## Effort check

- **Plan estimate:** M (~8h)
- **Reviewer estimate:** S–M (~6h) — confidence High
- **Why it differs / hidden complexity:** the rule and its tests exist; the second code PR for
    the deletion is gone. The operator task carries the uncertainty.

## Reuse & conflicts

- **Reuse:** `src/gold/property_group.py` and `src/gold/sql/pairs.sql` (the rule),
    `write_silver_tables` and `_silver_columns` in `src/silver/`, the pattern of
    `tests/test_silver_incremental.py:143`.
- **Conflict / coordinate with:** FEATURE-009 to FEATURE-012 change `process_landing` too; they
    start after this feature is merged.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-013-technical-plan.yaml`
- **Tasks:** 7; dependency graph and critical path verified yes
- **TDD contract:** RED failures and commands verified yes
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** M1, M2, M3 and M4 fixed in the feature plan and the technical plan
- **Optional:** L1 and L2 applied; L3 and L4 left as they are
- **Accepted residual risk:** an unknown reader of the Gold object, settled by the approval for
    its deletion
- **Technical plan:** emitted

## Next step

Run `/implementer FEATURE-013`.
