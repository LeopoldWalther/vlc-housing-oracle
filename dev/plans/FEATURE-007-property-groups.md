# FEATURE-007 — Property groups: one property behind several listings

**Status:** 🟡 In progress · **Effort:** M (~10h) · **Priority:** High
**Branch root:** `feature/property-groups` · **Created:** 2026-11-14 · **Updated:** 2026-11-14

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-007.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-007-technical-plan.yaml`.

## Objective

Publish, as the first Gold dataset, a mapping from every listing to a `property_group_id`, so
that listings which are certainly the same property count once. Without it every median, supply
count and time on market is distorted by one flat advertised by several agencies.

## Context

- **Current state:**
    - Silver deduplicates sightings of one listing id (`src/silver/sql/newest_sighting.sql`,
        `observation.sql`). Nothing relates two different listing ids to each other.
    - The data-quality exploration (FEATURE-005,
        [`docs/data-quality/silver-quality-2026-11.md`](../../docs/data-quality/silver-quality-2026-11.md))
        derived one "certain" duplicate rule from three manually judged samples: 29 of 29
        judgeable pairs were the same property, and every tested relaxation was uncertain.
    - Gold does not exist yet: there is no `src/gold/`, no Gold prefix and no convention beyond
        `CLAUDE.md` and ADR 0001 (plain Parquet under versioned prefixes, built from a recorded
        Silver snapshot).
- **Problem:** the rule exists only as prose and as tier counts. The findings bound its effect
    between two tiers and state that no committed query computes it. The rule is a pair rule and
    is not transitive, so "which listings are one property" needs groups, which nobody has built.
- **Constraints:**
    - Flag, never filter (user decision of 2026-10-30): no Silver row is changed or dropped; the
        grouping is an inferred label next to the facts.
    - The mapping is listing-level data. It stays in the private lake and is never published;
        only aggregates derived from it are.
    - Tests run on synthetic data only.

## Scope

- **In scope:**
    - the certain pair rule as tested SQL;
    - groups as connected components of those pairs, with a flag for groups that are held
        together only by a chain;
    - one Gold Parquet dataset, `listing_property_group`, with its provenance;
    - an operator command that builds it from a Silver catalog, and one real run.
- **Out of scope:**
    - a "possible" tier (the relaxations of sample 3): unmeasured beyond 50% to 70% precision;
    - choosing a representative listing or a price per group: that is each use case's decision;
    - the other quality labels of the findings (implausible values, misplaced coordinates,
        completeness per use case): a separate feature on the same Gold conventions;
    - any validation of pairs across sources (one source today; the rule itself ignores the
        source), and fuzzy matching of area or rooms;
    - repairing the missing one-room value upstream;
    - running the build automatically after every Silver run, and any infrastructure;
    - changing or expiring Silver snapshots.
- **Users / consumers:** every Gold feature and model that counts or prices properties, and the
    sequential re-listing analysis for liquidity.

## Dependencies

- **Needs:** FEATURE-004 (Silver in AWS) — complete. FEATURE-005 (the findings document this
    plan and the documentation cite as the rule's evidence) — complete.
- **Unblocks:** fair-price estimation, yield maps and market dynamics on distinct properties;
    together with FEATURE-002, time on market across re-listings.

## Open assumptions

The architect ran one read-only Athena query on 2026-11-14 against the current Silver `listing`
table (21,840 listings with a detail record: 16,342 sale, 5,498 rent). It returned the pairs of
the rule below as pairs of row numbers, never listing ids. The components were computed locally,
only counts were kept, and the pair list was deleted.

- **Assumption:** the rule yields enough groups to matter and few enough to be believable. —
    **Evidence:** 5,158 pairs (4,567 sale, 591 rent). 4,849 listings (22%) fall into 1,832
    groups, so 3,017 listings (13.8%) are surplus: 15.7% of sale and 8.2% of rent listings. That
    lies inside the bounds the findings derived from tiers B and C. 88% of the pairs are
    concurrent; 1,457 pairs rest on a missing floor and 53 involve a new build. —
    **If wrong:** nothing in the design changes; the operator run (4.2) records the real counts.
- **Assumption:** connected components do not chain unrelated flats together. — **Evidence:**
    1,645 of 1,832 groups (90%) are complete: every two members satisfy the pair rule. The other
    187 groups (914 listings) contain at least one pair that does not, and none is longer than
    three steps, so their members lie at most about 1 km apart and about 16% apart in price.
    66% of the groups have two listings; 63 have six or more and the largest has 19. —
    **If wrong:** chains are already flagged per group, so a consumer can exclude them; if they
    grow with more data, a follow-up splits incomplete groups.
- **Assumption:** the 19-listing group and its like are developments not marked as new build. —
    **Evidence:** unvalidated; the manual check never judged a group, only pairs. —
    **If wrong:** such groups are still the rule's honest output. The command reports the size
    distribution, `group_size` lets a consumer cap what it trusts, and the documentation says
    that groups of more than two listings are not manually validated (review M4, accepted).
- **Assumption:** a missing `rooms` may not match. — **Evidence:** `rooms` is missing for 2,358
    listings (10.8%), most of them probably one-room homes (findings document). The manual check
    judged no pair with a missing room count. — **If wrong:** duplicates among one-room homes are
    missed until the upstream defect is fixed; the rule never invents a group from missing data.
- **Assumption:** DuckDB computes the pairs fast enough without an index. — **Evidence:** the
    same self-join over 21,840 listings finished in seconds in Athena; DuckDB joins on the five
    equality keys first, which leaves 5,158 candidates to filter. — **If wrong:** the join is
    blocked by the equality keys already; nothing cheaper exists.

## Design

- **Primary approach:** a small `src/gold/` package with one dataset. An operator command reads
    Silver's `listing` at its current snapshot, computes the pairs in DuckDB SQL, turns pairs
    into groups with a union-find in Python, and writes one Parquet file.

- **The pair rule** (version `pg1`). Two listings with a detail record form a pair when all hold:
    - equal `operation`, `municipality`, `rooms`, `bathrooms` and `sqm_built`, none of them null;
    - the higher `price_eur` is at most 5% above the lower, compared in integers
        (`100 × higher ≤ 105 × lower`), so exactly 5% pairs on every machine;
    - both have coordinates and lie less than 350 m apart (haversine distance on a sphere of
        radius 6,371 km);
    - floor: if either `condition` is `new`, both floors are present and equal; otherwise the
        floors are equal or at least one is missing. A missing `condition` counts as resale.

    Not criteria: `source`, the time online, neighbourhood, district, `property_type` and
    `sqm_usable`. Two listings of different sources can pair. Sequential listings group too,
    which is what a re-listing needs. The thresholds are named constants in one place.

- **Groups.** A group is a connected component of the pair graph; a listing in no pair is its
    own group. Connected components are needed because the pair rule is not transitive and a
    partition of listings has to be.

- **The dataset.** `listing_property_group`, one row per listing with a detail record:

    | Column | Type | Meaning |
    | --- | --- | --- |
    | `source` | `VARCHAR` | As in Silver |
    | `source_listing_id` | `VARCHAR` | The listing |
    | `property_group_id` | `VARCHAR` | `<source>:<source_listing_id>` of the group's smallest member, ordered by `(source, source_listing_id)` |
    | `group_size` | `INTEGER` | Listings in the group; 1 for a listing without a duplicate |
    | `group_is_complete` | `BOOLEAN` | Every two members satisfy the pair rule. False marks a group held together by a chain |
    | `property_group_version` | `VARCHAR` | `pg1` |
    | `silver_listing_snapshot_id` | `BIGINT` | The Silver `listing` snapshot the build read |
    | `built_at` | `TIMESTAMP WITH TIME ZONE` | When the build ran, UTC |

    Counting distinct `property_group_id` counts properties, with no special case for listings
    without a duplicate.

- **Boundaries and data flow:** Silver `listing` (Iceberg, one pinned snapshot) → `src/gold/` →
    `<out>/listing_property_group/part-0.parquet`. Gold reads Silver through the catalog and
    never reads landing; Silver does not know Gold. The command takes the catalog name and the
    output location as arguments; the real location is the operator's private configuration.
    - **Reading:** the build takes the `listing` table's current snapshot id once and scans at
        exactly that id, so the recorded id is the one that was read even if a Silver run
        commits meanwhile (review M2).
    - **Writing:** `--out` is a directory or an `s3://` location, opened with PyArrow's own file
        system, which is already a dependency and uses the default credential chain. The
        versioned Gold prefix is part of `--out`, proposed as `gold/v1` beside `landing/` and
        `silver/` (review M3).

- **Contracts and invariants:**
    - **Provenance:** every row names its listing, the rule version, the Silver snapshot and the
        build time; the snapshot leads to the landing runs through Silver's own summary.
    - **Determinism:** rows are a pure function of the snapshot and the rule version, apart from
        `built_at`, which comes from an injected clock. A repeated build overwrites the file
        with the same rows.
    - **Stability of the id:** the id is stable for one snapshot. When a listing joins a group
        or two groups merge, the id can change with the next build. A consumer that needs a
        stable id pins the snapshot it built from, which the dataset states.
    - **Inferred, not fact:** the dataset says "certainly the same property under rule `pg1`".
        It never says sold, rented or re-listed.
    - **A rule change** is a new `property_group_version`, never an edit of `pg1`.

- **OOP / SOLID:** pure functions are sufficient: one that returns pairs from a listing table,
    one that returns groups from pairs, one that assembles and writes the dataset. The only
    boundaries are the catalog and the output location, both passed in.
- **Patterns:** None.
- **Deliberately rejected complexity:**
    - **The grouping as a Silver table.** It would run for free with every Silver run, but it is
        an inferred label, and the findings and the user's decision put labels next to the
        facts, not among them.
    - **A recursive SQL query for the components.** Fifteen lines of union-find are easier to
        read and to test than a recursive CTE over an undirected graph.
    - **Splitting incomplete groups** (cliques, diameter caps). 90% of groups are complete and
        the rest are flagged; a split needs a tie-break rule nobody has evidence for.
    - **A confidence column.** With one tier it would be a constant; the version names the rule.
    - **One immutable file per Silver snapshot plus a "latest" pointer.** Nothing reads an older
        build yet; the first training dataset that pins one introduces it.
    - **A spatial extension or index.** The haversine formula is one SQL macro.
    - **A generic Gold framework** (base classes, a dataset registry). One dataset does not show
        what a second one shares.

## Approach

Rule tasks test through the pair function on a few hand-written listing rows; no task reads real
data.

### Phase 1 — The pair rule

- [x] **1.1 A certain pair.**
    - RED: two listings equal in every key, priced 2% apart, 100 m apart, same floor, expect one
        pair. Fails because `src/gold/` does not exist.
    - GREEN: `pairs.sql` with the equality join, the price and distance conditions, and a
        haversine macro; a function that runs it on an Arrow table of listings.
    - REFACTOR: thresholds as named constants.
- [x] **1.2 What is not a pair.** One test per case, each expecting no pair:
    - a different operation, municipality, room count, bathroom count or built area;
    - a missing `rooms` on one side and on both sides;
    - a price gap just above 5%, with exactly 5% expected to pair;
    - a distance just above 350 m, with just below expected to pair;
    - missing coordinates; a listing without a detail record.
- [x] **1.3 The floor.**
    - RED: resale with equal floors, and with one floor missing, expect a pair; resale with
        different floors expects none. With a new build on either side: equal floors expect a
        pair; a missing or a different floor expects none. A missing `condition` behaves as
        resale.
    - GREEN: the floor condition.

### Phase 2 — Groups

- [x] **2.1 Components.**
    - RED: for pairs A–B and B–C the function expects one group of three whose id names the
        smallest member; two separate pairs expect two groups; no pairs expect no groups; the
        result does not depend on the order of the pairs.
    - GREEN: union-find.
- [x] **2.2 The dataset's rows.**
    - RED: listings forming a pair, a chain of three and two singles expect one row per listing
        with a detail record, the right `group_size`, `group_is_complete` false only for the
        chain, a singleton as its own complete group, and the provenance columns.
    - GREEN: the assembly function.

### Phase 3 — The Gold dataset

- [x] **3.1 Build from a Silver catalog.**
    - RED: over a SQLite catalog backfilled from synthetic landing, the build expects a Parquet
        file whose `silver_listing_snapshot_id` is the catalog's current `listing` snapshot and
        whose columns and types are the contract above; a second build gives the same rows; a
        catalog without a `listing` table is refused with a specific error.
    - GREEN: read the needed columns at the current snapshot, compute, write.
- [x] **3.2 A duplicate in the synthetic data.**
    - RED: the synthetic landing gains one scenario with the same property under two listing
        ids and a third unit of a new build on another floor; the build over it expects exactly
        one group of two and every other listing alone. Today no synthetic listing has
        coordinates, so none can pair (review M5). The existing Silver tests must stay green,
        which shows the scenario disturbs no other case.
    - GREEN: `_detail` in `src/synthetic/landing.py` accepts attribute overrides, and the
        scenario uses them for coordinates, floor and condition.
- [x] **3.3 The command.**
    - RED: `python -m src.gold.property_group --catalog <name> --out <dir>` expects exit 0, the
        file, and a report of counts only (listings, groups, surplus, incomplete groups, groups
        of more than five listings, largest group); a missing argument expects exit 2 and a
        refused catalog exit 1.
    - GREEN: the argument parser and the report.

### Phase 4 — Documentation and the first real run

- [x] **4.1 Documentation.** `docs/gold-property-group.md` with the rule, its evidence by
    reference to the findings, the columns, the stability and privacy notes, and the command;
    the README's status and architecture lines. Validation: leak guard and workflow validator.
- [ ] **4.2 Operator run.** After the merge and with explicit approval, because it writes the
    first object under a Gold prefix of the private bucket: build from the real catalog, and
    record the reported counts in
    `dev/plans/implementations/FEATURE-007-property-groups.md` beside the architect's evidence
    (1,832 groups over 4,849 listings, 187 incomplete, on the state of 2026-11-14).

## Files

- **Create:** `src/gold/__init__.py`, `src/gold/property_group.py` — pairs, groups, assembly,
    the build and the command.
- **Create:** `src/gold/sql/pairs.sql` — the pair rule.
- **Change:** `src/synthetic/landing.py` — one duplicate scenario.
- **Create:** `docs/gold-property-group.md`; **change:** `README.md`.
- **Tests:** `tests/test_gold_property_pairs.py` — phase 1.
- **Tests:** `tests/test_gold_property_groups.py` — phase 2.
- **Tests:** `tests/test_gold_property_group_build.py` — phase 3; `tests/test_synthetic_landing.py`
    for the scenario.
- **Create (4.2):** `dev/plans/implementations/FEATURE-007-property-groups.md`.

## Test strategy

- **Unit:** every criterion of the rule on both sides of its threshold; missing values; the
    floor matrix; components, order independence, the completeness flag.
- **Contract / fixtures:** the dataset's column names and types asserted once; the synthetic
    scenario.
- **Integration:** a local SQLite catalog backfilled from synthetic landing, the build writing
    into a temporary directory.
- **Retries / idempotency / evolution:** a repeated build gives the same rows; a missing table
    is refused. Schema evolution: not applicable, a change is a new version.
- **Configured quality checks:**
    - `python -m unittest discover -s tests -t .`
    - `ruff check src tests dev && ruff format --check src tests dev`
    - `python dev/tools/check_leaks.py`
    - `python dev/tools/validate_workflow.py`
- **Manual:** the operator run of 4.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract. The dataset is listing-level
    and private; the repository gains code, synthetic data and counts only.
- **Privacy / security:** listing ids and a derived group id, no free text, no coordinates, no
    prices in the output. The operator's existing rights to the private bucket suffice; no IAM
    change in code.
- **Observability:** the command's report. No metric or alarm, because nothing runs unattended.
- **Failure and recovery:** the file is written in one step; a failed build leaves the previous
    file. Deleting the file and building again restores it.
- **Migration / compatibility:** additive. Silver is not touched.

## Estimated monthly cloud cost

$0/month — no resources added or changed.

- **Cost drivers & cheaper alternatives:** one file of well under 1 MB in the existing bucket,
    and a read of a few megabytes of Silver per build. Building on a laptop costs nothing;
    running it after each Silver run in the existing Fargate task would add seconds of compute
    and is deliberately not part of this feature.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] The pair rule is tested on both sides of every threshold and for every floor case.
- [ ] Every listing with a detail record has exactly one row and one `property_group_id`, and
    the groups partition those listings.
- [ ] A group held together only by a chain is marked incomplete.
- [ ] The dataset names its rule version and the Silver snapshot it was built from, and a
    repeated build gives the same rows.
- [ ] The real run's counts are recorded and compared with the architect's evidence.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak and workflow checks pass
- [ ] The dataset's contract, privacy rule and command are documented
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Decided by the user (2026-11-14):** the dataset lives in Gold.
- **Decided at review (2026-11-14), open to the user's veto:** the build is run by hand until a
    consumer exists; automating it is a small follow-up on the existing task.
- **Question (user, before 4.2):** approve the first write under the Gold prefix; `gold/v1` is
    proposed.
- **Risk:** a large group is a development that is not marked as new build. — *Mitigation:*
    `group_size` and `group_is_complete` are on every row; the real run reports the largest
    groups as counts, and a privately judged sample of large groups can follow if they matter.
- **Risk:** the group id changes between builds and a consumer joins across builds. —
    *Mitigation:* the snapshot id is on every row and the documentation says to join within one
    build only.
- **Risk:** the rule misses a good third of true duplicates (findings document), so supply is
    still overstated. — *Mitigation:* stated in the documentation; a "possible" tier is the
    known next step and needs its own evidence.
- **Risk:** the evidence for the rule is 29 pairs. — *Mitigation:* the version makes the rule
    replaceable; every later manual check can be recorded against `pg1`.

## Progress log

- **2026-11-14** — Plan created. Pairs and components measured on the current Silver `listing`
    table, counts only; recorded under the open assumptions.
- **2026-11-14** — The user chose Gold. Review approved with corrections folded in:
    - M1: the rule ignores the source and the group id orders by `(source, source_listing_id)`;
    - M2: the build scans exactly the snapshot it records;
    - M3: the output is written with PyArrow's file system, the versioned prefix is part of
        `--out`, and the price comparison is in integers;
    - M4: group-level precision is an accepted, documented and reported limit;
    - M5: the synthetic detail records need coordinates before any synthetic pair can exist.

