# FEATURE-008 — Listing quality flags: implausible values and misplaced coordinates

**Status:** 🔵 Planned · **Effort:** M (~7h) · **Priority:** High
**Branch root:** `feature/listing-quality` · **Created:** 2026-11-15 · **Updated:** 2026-11-15

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-008.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-008-technical-plan.yaml`.

## Objective

Add a seventh Silver table, `listing_quality`, that gives every listing one flag per adopted
plausibility rule and per location rule of the data-quality findings. With these flags, a use case
can exclude implausible prices, areas, years and coordinates by a documented, versioned rule
instead of by ad-hoc filters.

## Context

- **Current state:**
    - The data-quality findings of FEATURE-005
        ([`docs/data-quality/silver-quality-2026-11.md`](../../docs/data-quality/silver-quality-2026-11.md))
        adopted ten plausibility rules and two location rules, with thresholds and flag rates.
        They exist only as counting expressions in
        [`analysis/silver-quality/02_plausibility.sql`](../../analysis/silver-quality/02_plausibility.sql)
        and [`03_location.sql`](../../analysis/silver-quality/03_location.sql): Athena SQL over a
        pinned snapshot, with no tests and no per-listing output.
    - Silver already holds one inferred table that is recomputed whole in every run:
        `listing_event` (FEATURE-002), built by `src/silver/sql/listing_event.sql` from Silver's
        own tables and written with `write_silver_tables` before the ingest log.
    - `listing_property_group` (FEATURE-007) is a Gold dataset. Its plan left "the other quality
        labels" to a separate feature.
- **Problem:** there is no per-listing flag that a use case can join on. Every consumer would
    re-derive the thresholds and get them subtly different.
- **Constraints:**
    - Flag, never filter (user decision of 2026-10-30): no existing Silver row is changed or
        dropped.
    - Layer (user decision of 2026-11-15): validation of an entity is Silver work in the
        medallion architecture, and Gold is for use-case-specific datasets. The flags are
        entity-level and use-case-neutral, so they live in Silver.
    - Tests run on synthetic data only.

## Scope

- **In scope:**
    - the ten adopted plausibility rules and the two location rules, one boolean flag each, as
        tested SQL;
    - the Silver table `listing_quality`, recomputed whole in every Silver run that processes
        something;
    - deployment of the new image and a record of the first real run.
- **Out of scope:**
    - moving `listing_property_group` from Gold to Silver: the same layer decision applies to it,
        and it is the next feature to plan;
    - the new-build-unit flag and the "possible duplicate" tier: unmeasured (user decision of
        2026-11-15);
    - completeness flags per use case: no use case is selected yet;
    - the dropped candidates of the findings: no rooms outside studios, sale priced like a rent,
        rent priced like a sale;
    - any combined score or "is usable" column: each use case chooses its flags;
    - repairing the missing one-room value upstream;
    - any change of infrastructure beyond the image tag.
- **Users / consumers:** every Gold feature and model that prices or segments listings; first
    of all, fair-price estimation.

## Dependencies

- **Needs:**
    - FEATURE-005: the rules, thresholds and expected rates.
    - FEATURE-002: the pattern of a Silver table recomputed whole from Silver.
    - Both are complete.
- **Unblocks:** fair-price estimation and fix-and-flip, whose minimum requirement in the findings
    is "no plausibility flag".

## Open assumptions

The architect ran one read-only Athena query on 2026-11-15 against the current Silver `listing`
table. Silver then held 22,877 listings with a detail record: 17,218 sale and 5,659 rent,
including the first run of 2026-11-15. The query returned counts only. It used the definitions
below, with an approximate median for the municipality centre.

- **Assumption:** the rules still flag tens of listings, not hundreds, as in the findings.
    - **Evidence:**

        | Rule | sale | rent |
        | --- | --- | --- |
        | price out of bounds | 0 | 0 |
        | built area below minimum | 0 | 0 |
        | built area above maximum | 5 | 0 |
        | price per m² below minimum | 26 | 3 |
        | price per m² above maximum | 5 | 4 |
        | usable area above built area | 0 | 0 |
        | rooms above maximum | 10 | 0 |
        | bathrooms above rooms plus two | 9 | 2 |
        | year built out of range | 5 | 0 |
        | price drop above maximum | 0 | 0 |
        | any plausibility flag | 57 (0.33%) | 9 (0.16%) |
        | coordinates missing | 139 | 44 |
        | farther than 10 km from the municipality centre | 135 | 9 |
        | any location flag | 274 (1.6%) | 53 (0.9%) |

        The findings' rates on the snapshot of 2026-10-31 were 0.4% and 0.2% for plausibility,
        and 1.6% and 0.9% for location.
    - **If wrong:** nothing in the design changes. The first real run (3.2) records the counts.
- **Assumption:** a municipality's median coordinate is a stable centre.
    - **Evidence:** 36 of 37 municipalities have ten or more located listings, and none of the
        144 listings beyond 10 km lies in the one smaller municipality.
    - **If wrong:** a municipality with very few listings has a centre that moves between runs.
        The table is recomputed whole, so the flag is always relative to the current listings.
- **Assumption:** DuckDB's exact `median` gives counts close to Athena's `approx_percentile`.
    - **Evidence:** unvalidated. A listing near the 10 km boundary may change sides.
    - **If wrong:** the far flag's count differs from 135 and 9 by a few listings. Task 3.2
        compares the table with the same rule written in Athena with an exact median, so that
        comparison does not depend on this assumption.
- **Assumption:** recomputing the table in every run costs no measurable time.
    - **Evidence:** the table has one row per listing (about 23,000) and one self-aggregation
        per municipality. `listing_event`, recomputed from far more rows, did not change the
        task duration of 21 s to 24 s.
    - **If wrong:** task 3.2 measures the duration. The fallback is to recompute only when
        `listing` changed, which is every run that processes a detail record.
- **Assumption:** the first run creates the table without new permissions.
    - **Evidence:** `listing_event` was created by the deployed task in the same namespace on
        2026-11-14 with the existing role.
    - **If wrong:** the run fails before the ingest log is written, so its runs stay pending.
        The role is extended and the run repeats.

## Design

- **Primary approach:** one more Silver statement, `src/silver/sql/listing_quality.sql`, handled
    like `listing_event`:
    - after a run has written its days and listings, the table is computed from Silver's current
        `listing`;
    - it is replaced whole with `write_silver_tables`;
    - the ingest log is still written last.

    The rule is one public function, `listing_quality(connection, listing)`, which takes the
    listing rows as an Arrow table, as `listing_events` takes its inputs. The table stands after
    `listing` in `SILVER_TABLES`, so `build_tables` computes it from the whole `listing` as the
    oracle of the equality tests.

    Every flag depends on one listing row, except the far flag, which depends on all listings of
    the municipality. A change of any listing can therefore move the flag of another, which is
    why the table is recomputed whole and not upserted by affected listing.

- **The rules** (thresholds from the findings, as named constants in one place).
    - **Inputs.** All inputs are integers in Silver, so every comparison is exact integer
        arithmetic. Price per m² is compared as `price_eur` against threshold × `sqm_built`,
        never as a division. Every product is computed as `BIGINT`: a 32-bit product overflows
        on exactly the extreme values the rules exist for, and DuckDB then fails the statement
        and with it the whole Silver run (review M2).
    - **Thresholds.** `rent` listings use the rent thresholds. Every other operation uses the sale
        thresholds, as the findings' queries do.

    | Flag | True when |
    | --- | --- |
    | `price_out_of_bounds` | `price_eur` below 10,000 or above 20,000,000 (sale); below 100 or above 30,000 (rent) |
    | `sqm_built_below_min` | `sqm_built` below 10 |
    | `sqm_built_above_max` | `sqm_built` above 2,000 |
    | `price_per_sqm_below_min` | `sqm_built` above 0 and `price_eur` below 300 × `sqm_built` (sale) or 2 × `sqm_built` (rent) |
    | `price_per_sqm_above_max` | `sqm_built` above 0 and `price_eur` above 15,000 × `sqm_built` (sale) or 60 × `sqm_built` (rent) |
    | `sqm_usable_above_built` | `sqm_usable` above `sqm_built` |
    | `rooms_above_max` | `rooms` above 12 |
    | `bathrooms_above_rooms` | `bathrooms` above `rooms` + 2 |
    | `year_built_out_of_range` | `year_built` below 1500, or above the UTC year of `detail_observed_at` plus one |
    | `price_drop_above_max` | `2 × price_eur` below `prev_price_eur`: a drop of more than 50% |
    | `coordinates_missing` | `latitude` or `longitude` is missing |
    | `far_from_municipality` | the haversine distance to the municipality centre is above 10 km |

    - **Missing inputs.** A flag is true only when its inputs are present and break the rule. A
        missing input gives false, never null, so a flag answers exactly "does this listing
        break rule X". Missing coordinates have a flag of their own; missing `rooms` is the
        known upstream defect, not a plausibility break.
    - **Year bound.** The upper bound follows the observation, not the clock of the run. The
        findings say "the calendar year plus one". Taking the year of the detail observation
        keeps a backfill identical to the incremental result and needs no clock inside the rule.
    - **Municipality centre.** The exact median `latitude` and median `longitude` of the
        municipality's listings with a detail record and coordinates, both operations together,
        as in the findings. The distance is the haversine distance on a sphere of 6,371 km. A
        listing without a municipality has no centre and is not far.
    - **What the flags mean.** A flag means "check", not "wrong". Per the findings, 17 of the 22
        low price-per-m² sale listings were houses with large plots.

- **The table.** `listing_quality`, one row per listing with a detail record, ordered by
    `(source, source_listing_id)`:

    | Column | Type | Meaning |
    | --- | --- | --- |
    | `source` | `VARCHAR` | As in `listing` |
    | `source_listing_id` | `VARCHAR` | The listing |
    | the twelve flags above | `BOOLEAN` | Never null |
    | `transformation_version` | `VARCHAR` | `silver-v1` |

    A consumer joins it to `listing` on the listing key.

- **Boundaries and data flow:**
    - landing → day tables → `listing` → `listing_quality`, all inside one Silver run.
    - The statement reads Silver's `listing` only. It never reads landing, and no other Silver
        statement reads `listing_quality`.

- **Contracts and invariants:**
    - **Provenance:** every row names its listing and the transformation version. The table's
        snapshot carries the same run properties as every other Silver table of that run.
    - **Incremental equals backfill:** the table is a pure function of `listing`, so any sequence
        of landing commits and a backfill give the same rows.
    - **Idempotency:** a repeated run replaces the table with the same rows. A run that stops
        before the ingest log leaves its runs pending, and repeating them is harmless.
    - **Inferred, not fact:** a flag says "this value breaks rule X". It changes no value of
        `listing`.
    - **Version:** existing tables do not change, so `transformation_version` stays `silver-v1`
        and no backfill is needed, as for `listing_event`. A later change of a threshold is a new
        version, like any rule change.

- **OOP / SOLID:** pure functions are sufficient: one returns the flag rows from a listing
    table, and `process_landing` calls it with Silver's current `listing`. No new boundary
    appears.
- **Patterns:** None.
- **Deliberately rejected complexity:**
    - **Flag columns on `listing` itself.** The far flag depends on other listings, so an upsert
        of affected listings would leave stale flags. A table of its own also keeps the facts
        free of inferred values.
    - **A Gold dataset** (the first draft of this plan). Rejected by the layer decision above.
    - **A separate `quality_version`.** Silver has one version for its rules, and a second one
        would have to be explained beside it. The table is recomputed whole, so a new version
        reaches every row in the next run.
    - **One row per (listing, flag).** Wide booleans are what a consumer filters on.
    - **An "any flag" column or a score.** A consumer ORs the flags it cares about.
    - **A fixed table of municipality centres.** No reference source exists in the repository.
        The median of the current listings is what the findings measured.
    - **Sharing the haversine macro with Gold's `pairs.sql`.** Silver must not depend on Gold.
        The statement defines its own macro; the two copies meet when the property groups move
        to Silver.

## Approach

Rule tasks test the flag function on a few hand-written listing rows; no task reads real data.

### Phase 1 — The rules

Each task adds columns that do not exist before it, so its test fails first (review M1).

- [ ] **1.1 The rows and nine plausibility flags.**
    - RED:
        - one row per listing with a detail record, and none for a listing without one;
        - rows are ordered by key and carry `transformation_version`;
        - for each of the nine plausibility flags other than the year flag:
            - a listing just over the threshold expects true;
            - one exactly at it expects false;
            - one with a missing input expects false, never null;
            - sale and rent are checked at their own bounds where they differ;
        - a built area of 300,000 m² and a price of 2,000,000,000 give flags, not an overflow.

        The test fails because the statement and its function do not exist.
    - GREEN: `listing_quality.sql` and `listing_quality()` in `src/silver/rebuild.py`.
    - REFACTOR: thresholds as named constants.
- [ ] **1.2 The year flag follows the observation.**
    - RED:
        - `year_built` equal to the observation year plus one expects false, and plus two
            expects true;
        - the same listing observed a year later expects false;
        - below 1500 expects true, exactly 1500 false, a missing year false.

        The test fails because the column does not exist.
    - GREEN: the year condition.
- [ ] **1.3 Location flags.**
    - RED:
        - a listing without coordinates expects `coordinates_missing` true and
            `far_from_municipality` false;
        - in a municipality of three close listings and one 11 km away, only the far one is
            flagged;
        - one 9 km away is not flagged;
        - the centre is the median, so the far listing does not move it;
        - the centre is computed per municipality;
        - a listing without a detail record does not count towards a centre;
        - a listing without a municipality is not far;
        - an empty listing table gives zero rows with the contract's columns and types.

        The test fails because the columns do not exist.
    - GREEN: the median centre and the distance condition.

### Phase 2 — In the Silver run

- [ ] **2.1 The run writes the table.**
    - RED, over a SQLite catalog and synthetic landing:
        - a backfill writes `listing_quality` with the columns and types above;
        - commits processed one at a time give the same rows as a backfill;
        - a catalog that predates the feature gets the table with its next pending run, and no
            other table's schema changes;
        - a run with nothing pending writes nothing.
    - GREEN: `listing_quality` in `SILVER_TABLES`, and `process_landing` computes it from
        Silver's `listing` and writes it before the ingest log.
- [ ] **2.2 Implausible values in the synthetic data.**
    - RED: the synthetic landing gains one scenario:
        - one listing with a built area of 2,500 m² and a construction year of 1400;
        - one listing in the twins' municipality about 15 km from them.

        A backfill over it expects exactly those flags on those listings. The existing Silver and
        property-group tests stay green, which shows the scenario disturbs no other case.
    - GREEN: the scenario, using the attribute overrides of `_detail`.
- [ ] **2.3 The command reports the table.**
    - RED: the command's output holds a `listing_quality: snapshot <id>` line after a run that
        processed something.
    - GREEN: the report line.

### Phase 3 — Documentation and deployment

- [ ] **3.1 Documentation.**
    - `docs/silver-rebuild.md`: the table in the table list, a `listing_quality` section with
        the rules, their evidence by reference to the findings and the "check, not wrong"
        reading, and the report line.
    - `README.md`: seven Iceberg tables.
    - The findings: a pointer from "Proposed rules" to the implemented rules, and a note that
        the labels live in Silver, not in Gold as its sketch proposed.
    - Validation: leak guard and workflow validator.
- [ ] **3.2 Deployment and first real run.**
    - Only after the merge and with explicit approval, because it changes the task definition:
        set the image tag, plan, apply.
    - After the first run that processes something:
        - compare the table with the same rule written independently in Athena, counts per
            flag only, with an exact median for the centre, so the counts must be equal
            (review M3);
        - compare the task duration with the 21 s to 24 s before;
        - record both in `dev/plans/implementations/FEATURE-008-listing-quality.md`, beside the
            architect's evidence of 2026-11-15.

## Files

- **Create:** `src/silver/sql/listing_quality.sql` — the rules.
- **Change:** `src/silver/rebuild.py` — the table in `SILVER_TABLES`, the function, the write in
    `process_landing`, the report line.
- **Change:** `src/synthetic/landing.py` — one implausible-values scenario.
- **Change:** `docs/silver-rebuild.md`, `README.md`,
    `docs/data-quality/silver-quality-2026-11.md`.
- **Tests:**
    - `tests/test_silver_listing_quality.py` — phase 1 and the run of 2.1;
    - `tests/test_silver_incremental.py` — the pre-feature catalog;
    - `tests/test_silver_cli.py` — the report line;
    - `tests/test_synthetic_landing.py` — the scenario.
- **Create (3.2):** `dev/plans/implementations/FEATURE-008-listing-quality.md`.

## Test strategy

- **Unit:**
    - every flag on both sides of its threshold and at the threshold itself;
    - sale and rent bounds;
    - missing inputs give false;
    - the year bound against the observation year;
    - the median centre per municipality and its robustness to the far listing.
- **Contract / fixtures:** the table's column names and types asserted once; the synthetic
    scenario.
- **Integration:** a local SQLite catalog; a backfill and one-at-a-time commits over synthetic
    landing.
- **Retries / idempotency / evolution:**
    - incremental equals backfill;
    - a repeated run gives the same rows;
    - a catalog without the table gains it without a backfill.
- **Configured quality checks:**
    - `python -m unittest discover -s tests -t .`
    - `ruff check src tests dev && ruff format --check src tests dev`
    - `python dev/tools/check_leaks.py`
    - `python dev/tools/validate_workflow.py`
- **Manual:** the deployment and comparison of 3.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract. The table is listing-level and
    private; the repository gains code, synthetic data and counts only.
- **Privacy / security:** the table holds listing ids and booleans. It has no free text, no
    coordinates and no prices. No IAM change.
- **Observability:** the command's report line. A failure of the statement fails the task, which
    the existing alarm reports.
- **Failure and recovery:** the table is written before the ingest log. A failed run leaves its
    runs pending and the previous table in place; the next run recomputes it.
- **Migration / compatibility:** additive. No existing table changes, and no backfill is needed.
    Until the first run with a pending commit after deployment, the table does not exist.

## Estimated monthly cloud cost

$0/month — no resources added or changed beyond a new task-definition revision.

- **Cost drivers & cheaper alternatives:**
    - The table adds one Iceberg write of well under 1 MB per Silver run and a fraction of a
        second of compute in the existing task.
    - Old snapshots of the table accumulate like those of `listing_event`; they are bytes, not
        dollars, at this size.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] Every flag is tested above, at and below its threshold, and with a missing input.
- [ ] Every listing with a detail record has exactly one row, and no flag is null.
- [ ] Any sequence of landing commits and a backfill give the same `listing_quality` rows.
- [ ] A catalog that predates the feature gains the table without a backfill, and no other
    table's schema changes.
- [ ] The first real run's counts per flag equal the same rule in Athena, and the task duration
    is recorded.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak and workflow checks pass
- [ ] The table's contract and reading are documented
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Decided by the user (2026-11-15):** plausibility and location flags only; the table lives in
    Silver.
- **Question (user, before 3.2):** approve the deployment of the new image.
- **Question (user, after this feature):** plan the move of `listing_property_group` to Silver
    as the next feature. Until then the two label sets live in different layers.
- **Risk:** the low price-per-m² flag mostly hits houses with plots and is read as "wrong". —
    *Mitigation:* the documentation states "check, not wrong" with the findings' house share.
    A use case can combine the flag with `property_type`.
- **Risk:** the municipality centre of a small municipality moves between runs. —
    *Mitigation:* today only one municipality has fewer than ten located listings, and none of
    them is far. The table always reflects the current listings.
- **Risk:** a threshold change bumps `transformation_version` for all of Silver. —
    *Mitigation:* accepted as Silver's existing rule. The thresholds rest on measured rates and
    are not expected to change often.
- **Risk:** the thresholds were chosen on five weeks of data. — *Mitigation:* task 3.2 records
    the rates on the current data, and the Athena rule can be rerun at any time.

## Progress log

- **2026-11-15** — Plan created as a Gold dataset. The rules were counted on the current Silver
    `listing` table, counts only, and recorded under the open assumptions. The user chose the
    scope: plausibility and location only.
- **2026-11-15** — Review approved with corrections folded in:
    - M1: phase 1 has three tasks, each adding columns that do not exist before it, so every
        test fails first;
    - M2: every product is computed as `BIGINT`, so an extreme value is flagged and cannot fail
        the Silver run;
    - M3: the Athena comparison of 3.2 uses an exact median, so the counts must be equal.
- **2026-11-15** — Replanned as a Silver table after the user's layer decision: entity-level
    validation belongs to Silver, Gold is for use-case-specific datasets. The move of
    `listing_property_group` follows as its own feature.
