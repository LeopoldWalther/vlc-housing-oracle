# FEATURE-005 — Silver data-quality exploration

**Status:** 🟡 In progress · **Effort:** M (~10h) · **Priority:** High
**Branch root:** `feature/silver-quality-exploration` · **Created:** 2026-10-30 · **Updated:** 2026-10-31

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-005.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-005-technical-plan.yaml`.

## Objective

Measure how trustworthy Silver is before any Gold feature or model is built on it: how complete
the listing attributes are, which values are implausible, how many listing ids describe the same
property (typically one flat advertised by several agencies), and which temporal or privacy
defects exist. The outcome is a versioned set of read-only exploration queries and a findings
document, holding aggregates only, that proposes concrete quality rules, with thresholds and
expected rates, for a follow-up feature to implement.

## Context

- **Current state:** Silver is maintained incrementally in AWS (FEATURE-004, complete). Its six
    Iceberg tables are registered in the Glue database `vlc_housing_silver` and are queryable in
    Athena (engine v3; the `primary` workgroup got a query-result location on 2026-10-27). The
    tables and their rules are documented in [`docs/silver-rebuild.md`](../../docs/silver-rebuild.md).
    No quality measurement of the real data exists beyond the equality check between incremental
    and backfill runs, which proves consistency, not plausibility.
- **Problem:** Every candidate use case (fair price, flip arbitrage, yield maps, liquidity,
    market dynamics) is distorted by duplicates and outliers: a flat advertised three times counts
    three times in a median, and a re-listed flat resets its time on market. Nobody knows yet how
    large these effects are, so neither Gold's filters nor a model's training set can be defined.
- **Constraints:**
    - This repository is public. Committed files hold queries, rules, and aggregate counts or
        rates only; never row values, listing ids, coordinates of single listings, text excerpts,
        or anything that would identify a listing.
    - Neither landing v3 nor Silver carries an advertiser or agency field (verified: no such
        column in `contracts/landing/v3/schema.json` or any Silver table). "The same flat from
        another agency" can therefore only be inferred from matching attributes, never observed.
    - **A collection sweep spans several days.** The scraper runs every three hours until every
        area has been collected, so one pass over the market takes about a week (stated by the
        user on 2026-10-30; the data agree: see the assumptions). A listing's `first_seen` during
        the first sweep is the day the scraper reached its area, not the day it was published.
        Its `last_seen` within the most recent sweep window does not mean it disappeared, and a
        gap of less than a sweep between two observations is the visiting rhythm, not a missed
        listing.
    - Following the user's decision of 2026-10-30, findings are **flagged, never filtered**: no
        Silver row is changed or dropped by this feature or by the rules it proposes; duplicates
        and outliers become inferred labels next to the facts.

## Scope

- **In scope:**
    - Athena (Trino SQL) exploration queries, one file per quality dimension, that read the
        current Silver snapshot and return aggregates only;
    - a privately run, manual precision check of the duplicate-candidate tiers, whose result is
        recorded as counts only;
    - a findings document with measured rates, proposed rules, thresholds, and a sketch of the
        follow-up feature.
- **Out of scope:**
    - production tables or code (`listing_quality` flags, a `property_cluster` mapping): the user
        chose exploration only, and they belong to a follow-up feature planned from the findings;
    - changes to Silver's transformations or to the redaction patterns (a defect found here
        becomes its own change with a new `transformation_version` or `redaction_version`);
    - a notebook environment: Athena already answers every question below, and a notebook would
        add a dependency and a real risk of committing outputs with real rows;
    - Gold, models, and any published aggregate beyond the findings document.
- **Users / consumers:** the operator (who runs the queries), and the Architect of the follow-up
    feature, who plans the quality labels from the findings.

## Dependencies

- **Needs:** FEATURE-004 — Silver in AWS, registered in Glue (complete).
- **Unblocks:** a follow-up feature that adds tested quality labels (outlier flags and a
    property-cluster mapping), and through it every Gold feature.

## Open assumptions

The architect ran read-only Athena queries on 2026-10-30 against the Silver state of that day
(about 19,000 listings, 18 observed days, a single source). Only aggregates were inspected.

- **Assumption:** Same-property duplicates across listing ids are frequent enough to matter. —
    **Evidence:** pairs of listings that share operation, municipality, built area, rooms,
    bathrooms, and asking price: about 4,100 sale pairs and 1,460 rent pairs. Of the sale pairs,
    about 3,000 lie within roughly 300 m of each other and about 2,650 of those also share the
    floor. About 93% of sale pairs are listed at the same time, and only about 6% share an identical
    description. That is the expected signature of one flat advertised by several agencies, each
    writing its own text. — **If wrong:** if the manual check (task 3.1) finds the near/same-floor
    tier mostly imprecise (for example, identical units in one new development), the rule needs
    further keys (year built, condition, features) or drops to "candidate" status.
- **Assumption:** An identical description is a weak duplicate signal. — **Evidence:** about 4,100
    pairs share a long description (over 200 characters), but about 80% of them differ in built
    area: developer or agency boilerplate reused across units. — **If wrong:** text identity gets
    a higher weight in the proposed rule.
- **Assumption:** Implausible values are rare, so flags will not dominate any analysis. —
    **Evidence:** price, built area, and price per m² have plausible 1st-99th percentile ranges
    for both operations. Rule candidates hit tens of listings, not hundreds: for example, about
    25 sale and 5 rent listings have a price per m² outside wide bounds, 10 have more than 12
    rooms, 11 have more bathrooms than rooms plus two, and 4 have a year built outside
    1500-2027. No coordinate is zero or outside the province bounds, and no sale is priced like a
    rent or the reverse. — **If wrong:** the rules get tighter; it does not change the design.
- **Assumption:** Coordinates are precise enough to compare listings at building level. —
    **Evidence:** about 720 rounded points (4 decimals, about 10 m) are shared by more than one
    listing, at most 24 on a single point. That suggests some portal-side snapping or obfuscation.
    — **If wrong:** the distance threshold of the duplicate rule widens, and the location
    dimension (task 2.3) documents the precision found.
- **Assumption:** Gaps in a listing's observed days reflect the multi-day sweep, not lost data. —
    **Evidence:**
    - **Revisit rhythm:** consecutive observations of a listing are a median of 7 days apart
        (25th-75th percentile: 5-9 days; 90th: 13).
    - **First sweep:** it ran from the first collection day to its seventh day (2026-09-23 to
        09-30, with daily `first_seen` counts between about 1,000 and 3,300) and first saw about
        14,200 listings. From the next day on, daily `first_seen` counts fall to tens or hundreds:
        genuinely new listings.
    - **Irregular collection:** pauses (2026-10-19 to 10-24, when the acquisition budget was
        exhausted) and days walking a single area stretch some intervals.

    About 15,400 of 19,000 listings have fewer observed days than their first-to-last span, which
    is expected under this rhythm. — **If wrong:** listings missed within a complete sweep are a
    collection defect and are reported to the acquisition repository. Task 2.5 measures this as
    "absent from a sweep that walked its area".
- **Assumption:** Redaction `r1` leaves no contact details in `listing_text`. — **Evidence:**
    **contradicted for one description** (refined by the review on 2026-10-31).
    - **What looked like a leak:** the architect's 8 "phone-like" matches came from a pattern
        without `r1`'s word boundary. All 8 sit inside longer digit runs; none is glued to a
        letter, and none stands alone.
    - **The likely real leak:** a mobile number written with a glued country code
        (`34` + 9 digits, no `+` or space) escapes `r1`, because the boundary `\b` before
        `[6789]` fails after a digit. One description matches this form.
    - **Other patterns:** 23 descriptions contain a web address; no e-mail address was found.

    — **If wrong:** if the glued form is confirmed, it is a privacy defect with its own change
    and a new `redaction_version` (`r2`), decided by the user outside this feature (review M6).

## Design

- **Primary approach:** plain SQL files run by hand in Athena against the Glue catalog. Each file
    answers one quality dimension, holds its thresholds as named constants in a leading `WITH`
    block, and selects only aggregates: counts, rates, and percentiles per operation and property
    type. Every table reference is pinned with `FOR VERSION AS OF <snapshot_id>`, which works in
    Athena on these tables (the review verified it on 2026-10-31). The snapshot ids sit in one
    constant block at the top of each file and are taken once per measurement round from
    `"<table>$snapshots"`, at a moment no Silver task is running. Reruns therefore return the same
    numbers, and no file mixes tables from different Silver runs (review M4). The findings document
    cites each file, the numbers it returned, and that snapshot set.
- **Boundaries and data flow:** Silver (Iceberg, Glue) → Athena → operator → findings document.
    Nothing writes to Silver, landing, or the catalog; the only side effect is Athena's result
    files under the workgroup's result prefix.
- **Contracts and invariants:**
    - **Read-only:** no query writes anything.
    - **Aggregates only:** every final `SELECT` groups to counts or rates. The privately run
        drill-downs needed for the manual check (task 3.1) are not committed.
    - **Duplicate tiers are explicit** (corrected by review M1, M3, M5):
        - **A0:** the shape key: operation, municipality, built area, rooms, bathrooms.
        - **A:** A0 plus an equal asking price.
        - **A±5 and A±10:** A0 plus asking prices within 5% and within 10%, so the price
            spread between agencies is measured rather than excluded by the key.
        - **B:** A±5 plus a distance below the threshold.
        - **C:** B plus an equal, non-null floor. A missing floor never matches; 31% of detail
            records have none.

        Each tier is reported separately as concurrent (the listings' observation spans overlap)
        or sequential, which is a re-listing.

        Distance and tolerance pairs are not transitive, so only key groups (A) are clusters. For
        tiers A±5 through C, the file reports pair counts, the listings in at least one pair, and
        bounds on the surplus. A connected-components clustering is the follow-up feature's job.
    - **Censored time windows:** each temporal query declares `RAMP_UP_END` and `CENSOR_START` as
        constants. `RAMP_UP_END` is the day by which every area had been walked at least once.
        For the current data that is 2026-09-30, the first collection day plus 7: the review
        measured all 106 areas first walked by then. Listings count as new only when their
        `first_seen` is strictly after it. An area first walked later gets its own ramp-up from
        its first walk (review L1). `CENSOR_START` is the last collection day minus 7 days.
        - Metrics that depend on when a listing appeared count only listings whose `first_seen`
            falls after `RAMP_UP_END`: new listings per day, time on market, re-listing as a
            sequential duplicate, and price changes since first sight.
        - Metrics that depend on when a listing disappeared count only listings whose `last_seen`
            falls before `CENSOR_START`.
        - A gap counts as missed only when a sweep walked the listing's area without seeing it.

        Cross-sectional dimensions (completeness, plausibility, location, concurrent duplicates)
        use all listings. Seven days is the user's figure; task 2.5 checks it against the measured
        revisit interval and records the value used.
- **OOP / SOLID:** not applicable. The deliverable is SQL and a document; there is no code with
    state, variants, or boundaries.
- **Patterns:** None.
- **Deliberately rejected complexity:**
    - a Python quality-report module with tests: it would build production structure before the
        rules are known, and the follow-up feature will build exactly that from settled rules;
    - fuzzy text similarity and image comparison: text identity already proved weak, and no
        image data crosses the landing contract;
    - geographic polygons for municipalities and barrios: per-municipality centroid distance is
        enough to find misplaced coordinates at this stage.

## Approach

The deliverable has no production behavior, so no task has a RED test. Each task is
validation-first: the query is first checked on a case whose answer is known (a sanity query or
a count that must agree with another source), and only then is its result recorded.

### Phase 1 — Frame

- [ ] **1.1 Exploration folder and rules.** Create `analysis/silver-quality/README.md`. It says how
    to run the files in Athena, that only aggregates may be committed, and which snapshot query
    records the data state. Validation: the leak guard passes on the new folder.

### Phase 2 — Measure, one file per dimension

- [ ] **2.1 Completeness.** `01_completeness.sql`: non-null rate of every attribute in `listing`
    by operation and property type; listings without a detail record; the empty-card share in
    `observation`. Validation: the detail-record count equals `count(detail_observed_at)` and the
    listing count equals the plain row count.
- [ ] **2.2 Plausibility.** `02_plausibility.sql`:
    - percentiles of price, built and usable area, price per m², rooms, bathrooms, year built,
        and community fee, by operation and property type;
    - counts per rule candidate: bounds per operation; usable area above built area; rooms 0
        outside studios; bathrooms above rooms plus two; year built outside a range; a price drop
        over 50% against `prev_price_eur`; a sale priced like a rent and the reverse;
    - disagreement between the observation's price and the detail's price on the same day.

    Validation: every rule count is re-derived once by an independent `count_if` in a second
    query.
- [ ] **2.3 Location.** `03_location.sql`:
    - coordinate completeness and the distribution of decimal places;
    - points shared by more than one listing;
    - distance of each listing from its municipality's median coordinate, as percentiles plus
        counts beyond thresholds;
    - consistency between `district` and `page_district`, and of `neighborhood` within a
        district.

    Validation: the province bounding-box count equals the earlier evidence (0).
- [ ] **2.4 Duplicates.** `04_duplicates.sql`:
    - cluster counts for tier A, and pair counts plus listings-in-pairs for tiers A0, A±5, A±10,
        B, and C, by operation;
    - the price-gap distribution within A0 pairs, which shows how far agencies' prices for one
        flat diverge;
    - the concurrent versus sequential split, where a sequential pair counts only when the later
        listing's `first_seen` is after `RAMP_UP_END` (a first-sweep listing has no known start);
    - cluster-size distribution;
    - the price spread inside clusters;
    - the share with identical redacted descriptions;
    - the surplus, meaning listings minus clusters (exact for A, bounded for the other tiers): how
        much a naive count overstates supply.

    Validation: the pair counts nest (A0 ≥ A±10 ≥ A±5 ≥ B ≥ C, and A0 ≥ A), and tier A's pair
    count agrees with the architect's evidence within the change since then.
- [ ] **2.5 Temporal.** `05_temporal.sql`:
    - **The sweep rhythm:** the revisit-interval percentiles and the days each sweep needs to
        walk every area (`walk`). These confirm or adjust the 7-day `RAMP_UP_END` and
        `CENSOR_START`.
    - **Per run and per day:** listings per run and per day, and the runs that contributed no
        observation. This separates "deduplicated away by day" from "the run delivered nothing".
    - **Missed listings:** listings absent from a sweep that walked their area.
    - **Listings after the ramp-up only:**
        - new listings per day;
        - price-change frequency in `listing_detail`;
        - listings that vanish and return;
        - with the censored end, the distribution of observed listing lifetimes.

    Validation: the run count equals `landing_ingest`'s row count, and the measured median
    revisit interval agrees with the sweep length the window assumes. Missed listings are then
    either explained by the walk coverage or reported to the acquisition repository.
- [ ] **2.6 Redaction residue.** `06_redaction.sql` (corrected by review M6) counts what `r1`'s
    exact patterns, copied from `src/silver/sql/redact.sql`, leave behind:
    - phone digits classified by their surroundings: standalone, glued to a letter, glued
        country code (`34` + 9 digits), or inside a longer number;
    - e-mail and web-address patterns;
    - honorific-plus-name forms that `r1` misses, such as lower-case or all-caps names.

    Validation: the same expressions, run on the strings of `tests/test_silver_redaction.py`,
    find nothing, because `r1` redacts all of them. A hand-written glued `34…` string is found.
    A confirmed leak is reported to the user at once as a privacy issue and is not left for the
    findings document.

### Phase 3 — Judge and report

- [ ] **3.1 Manual precision of duplicate tiers** (operator task; corrected by review M2).
    - **The sample:** the operator privately draws 30 pairs from each exclusive stratum, so each
        stratum's precision is known on its own: C; B without C; A±5 without B; A0 without A±10.
        The pairs are ordered by a hash of their ids, not chosen by hand, and only concurrent
        pairs are drawn, because a vanished listing's page may be gone.
    - **The judgment:** for each pair the operator decides, from the listing pages, whether it is
        the same property: photos, floor plan, description.
    - **What is recorded:** counts per stratum only (same / different / unsure / page gone). The
        sample and its ids stay outside the repository.
- [ ] **3.2 Findings document.** `docs/data-quality/silver-quality-2026-11.md` covers:
    - the Silver snapshot ids;
    - per dimension, the measured rates and what they mean for each candidate use case;
    - the redaction result;
    - the proposed rules: duplicate tier and thresholds by precision, outlier bounds, and
        completeness requirements per use case;
    - the expected flag rates;
    - a sketch of the follow-up feature: a labels table with a `quality_version`, flagged and
        never filtered.

    Validation: every number cites the query file that produced it; the leak guard passes.

## Files

- **Create:** `analysis/silver-quality/README.md` — how to run, privacy rule, snapshot recording.
- **Create:** `analysis/silver-quality/01_completeness.sql` … `06_redaction.sql` — one dimension
    each, aggregates only.
- **Create:** `docs/data-quality/silver-quality-2026-11.md` — findings and proposed rules.
- **Change:** `dev/plans/README.md` — feature row (done with this plan).
- **Not committed:** the sampling query and drill-downs for task 3.1.
- **Tests:** none. No production behavior changes; the existing suite must stay green.

## Test strategy

- **Unit:** N/A. No production code; the follow-up feature carries tests for every rule it
    adopts.
- **Contract / fixtures:** N/A.
- **Integration:** N/A.
- **Retries / idempotency / evolution:** queries are read-only and pinned to a recorded snapshot,
    so a rerun on the same snapshot returns the same numbers.
- **Configured quality checks:**
    - `python -m unittest discover -s tests`
    - `ruff check .` and `ruff format --check .`
    - `python dev/tools/check_leaks.py`
    - `python dev/tools/validate_workflow.py`
- **Manual:** each query's validation step in the Approach section, and the duplicate precision
    check (3.1).

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract; the findings publish only
    aggregates.
- **Privacy / security:**
    - Committed files must not contain row values. The manual sample and every drill-down stay
        private.
    - Task 2.6 can surface contact details in Silver; a confirmed case is handled as a privacy
        defect with a new redaction version.
    - The user's existing IAM rights suffice; Lake Formation does not govern the database.
- **Observability:** N/A. The follow-up feature decides whether quality rates become a metric.
- **Failure and recovery:** N/A. Read-only.
- **Migration / compatibility:** N/A.

## Estimated monthly cloud cost

$0/month — no resources added or changed. Ad-hoc Athena queries scan Silver's Parquet files (tens
of MB per full scan) at $5 per TB, about $0.0005 per query, so even hundreds of queries cost
cents. Result files under the workgroup's result prefix are a few KB each.

- **Cost drivers & cheaper alternatives:** none relevant. DuckDB with PyIceberg on a laptop would
    avoid Athena entirely but adds credential setup on the laptop for no saving.
- **External / non-AWS costs:** none.
- **Budget check:** yes, negligible.

## Success criteria

- [ ] Six exploration queries exist, each returning aggregates only, and each passed its
    validation step on a recorded Silver snapshot.
- [ ] The duplicate tiers A, B, and C are measured by operation, split into concurrent and
    sequential, and their precision is known from at least 30 manually judged pairs per tier.
- [ ] Each plausibility rule candidate has a measured hit count and a proposed decision (adopt
    with threshold, or drop).
- [ ] The redaction residue is measured and every confirmed contact detail is reported as a
    privacy issue.
- [ ] The findings document proposes the follow-up feature's rules, thresholds, and expected flag
    rates, citing the source query for every number.
- [ ] The existing suite, ruff, leak guard, and workflow validator pass.
- [ ] No row values, listing ids, single-listing coordinates, or text excerpts are committed.

## Open questions & risks

- **Question:** Were the five runs on 2026-10-25 that delivered no observations a collection
    defect? Task 2.5 measures it in Silver; the cause lives in the acquisition repository.
- **Risk:** Tier B and C may group identical units of a new development, which are different flats
    with the same attributes. — *Mitigation:* the manual precision check, and splitting
    concurrent listings in the same building (equal coordinates) from those merely near each other.
- **Risk:** A committed query or document accidentally carries a row value. — *Mitigation:* every
    final `SELECT` aggregates; review checks it; the leak guard catches named identifiers, though
    not values.
- **Risk:** A pause in collection (as on 2026-10-19 to 10-24) makes a sweep longer than seven
    days. Listings then look missed or vanished when they were simply not visited. — *Mitigation:*
    "missed" and "vanished" are judged against sweeps that actually walked the listing's area,
    never against the calendar alone.
- **Risk:** The data are young (18 observed days, and only about three sweeps after the ramp-up). Temporal findings (re-listings, price changes)
    are early and are re-measured before a liquidity use case relies on them. — *Mitigation:* the
    queries are versioned and rerunnable; the findings name their snapshot.

## Progress log

- **2026-10-30** — Plan created. The user chose exploration only (no production tables) and
    flag-never-filter for later rules. The architect's Athena evidence on that day's snapshot
    showed: frequent attribute-key duplicate pairs, mostly concurrent and mostly with different
    texts; rare implausible values; somewhat snapped coordinates; and 8 descriptions with a
    phone-like pattern after `r1`.
- **2026-10-30** — The user pointed out that a sweep takes several days (the scraper runs every
    three hours until everything is collected), so temporal analysis starts about 7 days after the
    first collection day. The data confirm this: the first sweep (days 1-7) first saw about 14,200
    listings, and the median revisit interval is 7 days. The plan now declares a ramp-up window
    and a censored end for every temporal metric, and the gap question is closed.
- **2026-10-31** — Review approved with corrections folded in:
    - M1: price-tolerant duplicate tiers;
    - M2: exclusive sampling strata;
    - M3: clusters only for key groups;
    - M4: snapshot-pinned queries;
    - M5: a null floor never matches;
    - M6: the redaction evidence is refined (one glued `34…` number escapes `r1`);
    - L1: the ramp-up is tied to the measured sweep completion.

    The effort grows to ~10h for the manual check.
