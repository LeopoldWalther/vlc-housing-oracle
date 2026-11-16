# FEATURE-013 — Property groups move from Gold to Silver

**Status:** 🟡 In progress · **Effort:** M (~6h) · **Priority:** High
**Branch root:** `feature/property-groups-in-silver` · **Created:** 2026-11-16 · **Updated:** 2026-11-16

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-013.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-013-technical-plan.yaml`.

## Objective

Maintain `listing_property_group` as a Silver table that every Silver run recomputes, and remove
the Gold command that builds it by hand. The grouping is an entity-level label like the quality
flags, and by the layer decision of 2026-11-15 such labels belong to Silver. Today the Gold file
is as old as its last manual build.

## Context

- **Current state:**
    - `src/gold/property_group.py` builds `gold/v1/listing_property_group/part-0.parquet` from
        the current snapshot of Silver's `listing` when the operator runs it (FEATURE-007). The
        rule `pg1` is `src/gold/sql/pairs.sql` plus connected components in Python.
    - The first and only real build ran on 2026-11-14: 21,840 rows, 1,832 groups of two or more
        with 4,849 listings, 187 incomplete groups.
    - `listing_quality` and `listing_event` are Silver tables recomputed whole from Silver in
        every run that processes something, written together before the ingest log.
- **Problem:**
    - Silver has gained 1,319 listings with a detail record since that build; the Gold file does
        not know them.
    - Two label sets for one entity live in two layers, with two ways of being built.
    - Nothing reads the Gold file yet, so the move costs no consumer anything now.
- **Constraints:**
    - The rule `pg1` and its thresholds do not change.
    - Layer decision of the user (2026-11-15), and this feature first (2026-11-16).
    - Deleting the Gold object is a change in AWS and needs approval.

## Scope

- **In scope:**
    - the Silver table `listing_property_group`, written by every run that processes something;
    - the rule's code and tests under `src/silver/` and `tests/`;
    - removal of the Gold command, its documentation and, with approval, the Gold object;
    - deployment and a record of the first run against one last Gold build.
- **Out of scope:**
    - any change of rule `pg1`, a "possible" tier, or splitting incomplete groups;
    - a stable `property_group_id` across runs;
    - a representative listing or a price per group: each use case's choice;
    - removing the Gold layer from the architecture: it stays, without a dataset for now.
- **Users / consumers:** every dataset that counts or prices properties; the later price history
    per property.

## Dependencies

- **Needs:** FEATURE-007 and FEATURE-008 — complete.
- **Unblocks:** FEATURE-009 to FEATURE-012 are implemented after it, because all change the same
    function. Gold datasets can join facts and all labels in one layer.

## Open assumptions

- **Assumption:** computing the groups in every run is cheap enough. — **Evidence:** review of
    2026-11-16: the rule's equality join yields 133,139 candidate pairs on 23,159 listings; a
    local spike on synthetic rows with six times as many candidates took 0.14 s for the pairs
    and 0.29 s for groups and rows. Unmeasured in the task. — **If wrong:** task 3.2 measures
    it; the fallback is to skip the recompute when a run changed no listing.
- **Assumption:** nothing reads the Gold file. — **Evidence:** the repository has no reader, and
    the README calls the dataset private and built by hand. — **If wrong:** the user says so at
    review; the Gold object would then stay until the reader moves.
- **Assumption:** the lineage columns of the Gold file are not needed in Silver. —
    **Evidence:** `silver_listing_snapshot_id` and `built_at` recorded which Silver state a
    manual build read. A Silver table is written in the same run as `listing`, and its Iceberg
    snapshot carries the run's properties. — **If wrong:** adding a column later is a backfill.

## Design

- **Primary approach:**
    - The three functions of the rule are created in `src/silver/property_group.py` as copies
        that keep their behaviour: `duplicate_pairs`, `group_of`, `property_group_rows`. The
        statement is copied to `src/silver/sql/pairs.sql`; only the name of the relation it
        reads changes. The module does not import `src.silver.rebuild`.
    - `process_landing` reads Silver's `listing` once after the run has written its listings,
        with the columns both label rules need, and computes `listing_quality` and the property
        groups from that one read. It writes them with `listing_event` in the one
        `write_silver_tables` call before the ingest log.
    - The table is not part of `SILVER_TABLES`, which names the tables `build_tables` computes
        by one statement each. A constant `PROPERTY_GROUP_TABLE` names it.
    - The Gold module, its command and its tests are deleted in the same pull request, once the
        pipeline test covers the synthetic duplicate. The reference for the comparison of the
        first real run is the rule at the commit before the merge; the Gold object stays until
        that comparison has passed.
- **Contracts and invariants:**
    - One row per listing with a detail record, ordered by key: `source`, `source_listing_id`,
        `property_group_id`, `group_size`, `group_is_complete`, `property_group_version`,
        `transformation_version`.
    - `property_group_version` stays `pg1`; a rule change is a new version, never an edit.
    - `property_group_id` is stable within one snapshot only, as documented today.
    - The rows are a pure function of `listing`, so incremental runs equal a backfill.
- **OOP / SOLID:** pure functions are sufficient. The build command's report and error classes
    go with the command.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - expressing the components in SQL to fit the one-statement shape of the other tables: a
        recursive query is harder to read than the tested Python function;
    - keeping the Gold command as a second writer;
    - a compatibility view under the old Gold path.

## Approach

### Phase 1 — The rule under Silver

- [ ] **1.1 The pair rule.** RED: the pair tests, copied from the Gold tests, import
    `duplicate_pairs` from `src.silver.property_group`; they fail because the module does not
    exist. GREEN: the function, the thresholds as named constants and the copied statement.
- [ ] **1.2 Groups and rows.** RED: the group and row tests import `group_of` and
    `property_group_rows` and expect the Silver columns with `transformation_version`; they fail
    on the missing names.

### Phase 2 — The run writes the table

- [ ] **2.1 Pipeline.** RED: after a backfill of the synthetic landing the catalog holds
    `listing_property_group` with the synthetic duplicate in one group; stepwise commits give
    the same rows; a catalog that predates the table gains it without a backfill; a table
    overwritten with every listing as its own group is corrected by the next run.
- [ ] **2.2 Report.** RED: the command prints `listing_property_group: snapshot <id>`.
- [ ] **2.3 Remove the Gold code.** Delete `src/gold/` and the three Gold test files.
    Validation: the suite is green and no file under `src` or `tests` names `src.gold`.

### Phase 3 — Operation

- [ ] **3.1 Documentation.** The table's section in `docs/silver-rebuild.md` takes over the rule,
    the columns and the limits; `docs/gold-property-group.md` is deleted; `README.md` and the
    findings name the table under Silver and no Gold dataset.
- [ ] **3.2 Deployment, first run and the Gold object (operator, with approval).** Deploy. After
    the first run, apply the rule of the commit before the merge in memory to the current
    `listing` snapshot and compare it with the Silver table, as counts only: rows, groups of two
    or more, listings in a group, incomplete groups, largest group, and the number of listings
    whose group differs. Nothing is written. Then, with its own approval, delete the Gold
    object. Record the counts and the task duration before and after in
    `dev/plans/implementations/FEATURE-013-property-groups-in-silver.md`.

## Files

- **Create:** `src/silver/property_group.py`, `src/silver/sql/pairs.sql`.
- **Change:** `src/silver/rebuild.py`, `docs/silver-rebuild.md`, `README.md`,
    `docs/data-quality/silver-quality-2026-11.md` (the pointer to the implemented rule).
- **Delete:** `src/gold/`, `tests/test_gold_property_pairs.py`,
    `tests/test_gold_property_groups.py`, `tests/test_gold_property_group_build.py` (2.3);
    `docs/gold-property-group.md` (3.1).
- **Tests:** `tests/test_silver_property_pairs.py`, `tests/test_silver_property_groups.py` (new,
    from the Gold tests), `tests/test_silver_incremental.py`, `tests/test_silver_rebuild.py`,
    `tests/test_silver_cli.py`.

## Test strategy

- **Unit:** the existing pair and group cases, unchanged in content.
- **Contract / fixtures:** the table's columns; the synthetic duplicate of FEATURE-007.
- **Integration:** a local SQLite catalog with a backfill and stepwise commits.
- **Retries / idempotency / evolution:** incremental equals backfill; a catalog that predates
    the table; a repeated run.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`.
- **Manual:** the comparison of 3.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract.
- **Privacy / security:** ids and group labels only. No IAM change; the task role already writes
    Silver.
- **Observability:** the report line; a failing rule fails the task and raises the existing
    alarm.
- **Failure and recovery:** written before the ingest log; a failed run leaves its runs pending.
- **Migration / compatibility:** additive in Silver, no backfill. The Gold code leaves with the
    pull request; the Gold object is removed only after the comparison.

## Estimated monthly cloud cost

$0/month — no resources added or changed beyond a new task-definition revision.

- **Cost drivers & cheaper alternatives:** some seconds of compute per run in the existing task;
    one more Iceberg write of under 1 MB.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] Every Silver run that processes something writes `listing_property_group`, one row per
    listing with a detail record.
- [ ] Any sequence of commits and a backfill give the same rows.
- [ ] The first real run equals the rule of the commit before the merge, applied to the same
    `listing` snapshot, in every recorded count.
- [ ] No Gold code, test, document or object of the dataset remains.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak and workflow checks pass
- [ ] The table, its rule and its limits are documented under Silver
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (user, in 3.2):** approve the deployment, and separately the deletion of the Gold
    object.
- **Risk:** the recompute lengthens every run. — *Mitigation:* measured in 3.2, with the skip
    for unchanged listings as the fallback.
- **Risk:** group ids change from run to run and a consumer joins across runs. —
    *Mitigation:* documented as today; a consumer pins the table's snapshot.
- **Risk:** the corrected one-room extraction of the acquisition side changes the groups when
    its replay lands. — *Mitigation:* intended; the table follows `listing` in the same run.

## Progress log

- **2026-11-16** — Plan created. The user asked for this feature first, before FEATURE-009 to
    FEATURE-012.
- **2026-11-16** — Review approved with four corrections folded in: the Gold code is deleted in
    the same pull request and the reference is the commit before the merge (M1); the replacement
    test needs no generator change (M2); the tests that enumerate Silver's tables and one read
    of `listing` for both label rules (M3); the comparison runs in memory after the first run,
    on one snapshot (M4). Task 3.3 is folded into 3.2.
