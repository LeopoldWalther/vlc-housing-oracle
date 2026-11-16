# FEATURE-009 — Listing price history

**Status:** 🟢 Complete · **Effort:** S (~6h) · **Priority:** High
**Branch root:** `feature/listing-price-history` · **Created:** 2026-11-16 · **Updated:** 2026-11-16

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-009.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-009-technical-plan.yaml`.

## Objective

Give every listing its observed price history as a Silver table, `listing_price`, whose last row
is the current price. Today the price of a listing is the price of its one detail record, which is
stale for 17% of sale listings.

## Context

- **Current state:**
    - `listing` takes every attribute, including `price_eur`, from the chosen detail
        record ([`src/silver/sql/listing.sql`](../../src/silver/sql/listing.sql)). From
        `observation` it takes only `first_seen`, `last_seen` and `days_observed`.
    - `observation` holds one search-card row per listing and day, with `price_eur` and
        `prev_price_eur`.
    - `listing_event` and `listing_quality` are Silver tables recomputed whole from Silver in
        every run that processes something (FEATURE-002, FEATURE-008).
- **Problem** (aggregate counts on Silver of 2026-11-15, 23,159 listings):
    - 23,081 listings have exactly one detail record, and 52% of sale listings were last seen
        more than 30 days after it. The detail price differs from the last search-card price
        for 2,942 sale and 693 rent listings (17% and 12%), by a median of 4.7% and 7.1%.
    - The search cards show 4,389 price changes on 3,651 listings; 600 listings changed more
        than once, and 36 returned to an earlier price. No table holds them.
- **Constraints:**
    - Money stays integer euros; no stored percentage.
    - No existing table changes, so the feature needs no new `transformation_version`.
    - Tests run on synthetic data only.

## Scope

- **In scope:**
    - the Silver table `listing_price`: one row per listing and price period;
    - deployment and a record of the first run.
- **Out of scope:**
    - replacing `listing.price_eur`: it stays the detail record's price, and a consumer joins
        the current row of `listing_price`;
    - `rooms` from the search card: the acquisition side now reads the one-room wording
        (extraction version 3, 2026-11-16), and its replay fills history without a Silver rule;
    - any other attribute from the search card (`sqm_built` differs for 276 listings and
        `property_type` for 65; no rule says which side is right);
    - fetching detail records again: the search card already shows a changed price (user
        decision of 2026-11-16).
- **Users / consumers:** fair-price estimation, market dynamics, and the later deal scout, which
    all need the current price and the size of a reduction.

## Dependencies

- **Needs:** FEATURE-004 and FEATURE-008 — complete. Implemented after FEATURE-013, which
    changes the same function.
- **Unblocks:** every Gold dataset that prices listings.

## Open assumptions

- **Assumption:** the card's declared previous price is a fact worth keeping beside the observed
    one. — **Evidence:** of 3,135 observed reductions whose card declares a previous price, 2,991
    declare exactly the price observed before. 2,066 listings declare a previous price on their
    first sighting, a reduction from before collection began. — **If wrong:** the column is
    separate from the observed one and can be ignored.
- **Assumption:** the recompute adds no noticeable time. — **Evidence:** `listing_event` already
    reads all of `observation` in every run. — **If wrong:** measured in task 3.2.

## Design

- **Primary approach:**
    - `src/silver/sql/listing_price.sql`, handled like `listing_event`: a member of
        `SILVER_TABLES` after it, computed from Silver's own `observation` and `listing_detail`
        after a run has written its days, replaced whole, before the ingest log.
    - The run reads `walk`, `observation` and `listing_detail` from Silver once, before the
        event rule and the price rule run, and passes each rule its columns: each rule narrows
        the session's temporary tables to what it reads.
    - A price sighting is a search card that is not empty and has a price, or a detail record
        with a price. Sightings of one listing are in a total order: `observed_at`, then cards
        before detail records, then `price_eur`, then `prev_price_eur` with nulls last. A period
        is a maximal run of consecutive sightings with one price. A return to an earlier price
        is a new period.
- **Contracts and invariants:**
    - `listing_price`, ordered by `source`, `source_listing_id`, `period`:

        | Column | Type | Meaning |
        | --- | --- | --- |
        | `source`, `source_listing_id` | string | the listing |
        | `period` | int | 1 for the first observed price, rising |
        | `price_eur` | int | the price of the period |
        | `first_observed_at`, `last_observed_at` | timestamp (UTC) | first and last sighting |
        | `sightings` | int | sightings in the period |
        | `previous_price_eur` | int | price of the period before; null for period 1 |
        | `declared_previous_price_eur` | int | the `prev_price_eur` of the period's first sighting, card or detail record; null without one |
        | `is_current` | boolean | true for the listing's last period |
        | `transformation_version` | string | |

    - Every listing with a price sighting has exactly one current row. `is_current` says "the
        last price seen", not "still online"; that is `listing_event`.
    - The table is a pure function of Silver's day tables, so incremental runs equal a backfill.
- **OOP / SOLID:** pure functions and SQL statements are sufficient.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - a separate `current_price` table: it is the rows with `is_current`;
    - merging the declared previous price into the periods: it is the portal's statement, not
        an observation.

## Approach

### Phase 1 — Price periods

- [x] **1.1 Periods from sightings.**
    - RED: one price over three days gives one row with three sightings; a change gives two
        rows; A, B, A gives three; empty cards and cards without a price are ignored; a listing
        with a detail record and no card has one period; a card and a detail record at one
        instant with different prices give the card's period first; two detail records at one
        instant give the lower price first; exactly one row per listing is current. Fails
        because the function does not exist.
    - GREEN: `listing_price.sql` and `listing_prices()` in `src/silver/rebuild.py`.
- [x] **1.2 Previous prices.**
    - RED: `previous_price_eur` is the price of the period before and null for period 1;
        `declared_previous_price_eur` is the `prev_price_eur` of the period's first sighting,
        card or detail record, and null without one. Fails on the missing columns.

### Phase 2 — The run writes the table

- [x] **2.1 Pipeline.** RED: after a backfill of the synthetic landing the catalog holds
    `listing_price`; stepwise commits give the same rows; a catalog that predates the table
    gains it without a backfill.
- [x] **2.2 Synthetic scenario and report.** RED: the generator holds a listing that returns to
    its first price (a reduction exists already); the command prints
    `listing_price: snapshot <id>`.

### Phase 3 — Documentation and operation

- [x] **3.1 Documentation.** `docs/silver-rebuild.md` (the table and its reading), `README.md`.
- [x] **3.2 Deployment and first run (operator, with approval).** New image, then counts only:
    periods, changes and current rows against the same rule in Athena, and the task duration.
    Recorded in `dev/plans/implementations/FEATURE-009-listing-price-history.md`.

## Files

- **Create:** `src/silver/sql/listing_price.sql`.
- **Change:** `src/silver/rebuild.py`, `src/synthetic/landing.py`, `docs/silver-rebuild.md`,
    `README.md`.
- **Tests:** `tests/test_silver_listing_price.py` (new), `tests/test_silver_incremental.py`,
    `tests/test_silver_cli.py`, `tests/test_synthetic_landing.py`.

## Test strategy

- **Unit:** periods, returns, ignored sightings, ordering within a day.
- **Contract / fixtures:** column names and types of the table; the synthetic scenarios.
- **Integration:** a local SQLite catalog with a backfill and stepwise commits.
- **Retries / idempotency / evolution:** incremental equals backfill; a repeated run; a catalog
    that predates the table.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`.
- **Manual:** the comparison of 3.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract.
- **Privacy / security:** ids, prices and instants only. No IAM change.
- **Observability:** the report line; a failing statement fails the task and raises the
    existing alarm.
- **Failure and recovery:** written before the ingest log; a failed run leaves its runs pending.
- **Migration / compatibility:** additive; no backfill.

## Estimated monthly cloud cost

$0/month — no resources added or changed beyond a new task-definition revision.

- **Cost drivers & cheaper alternatives:** one more Iceberg write of under 1 MB per run.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [x] Every listing with a price sighting has its periods in `listing_price` and exactly one
    current row.
- [x] Any sequence of commits and a backfill give the same rows.
- [x] The first real run's counts equal the same rule in Athena.
- [x] Every behavior was implemented test-first and focused plus affected suites pass
- [x] Configured lint, format, leak and workflow checks pass
- [x] The table is documented
- [x] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (user, before 3.2):** approve the deployment.
- **Risk:** weekly sightings hide short-lived prices. — *Mitigation:* documented; a period is
    what was observed, not everything that happened.

## Progress log

- **2026-11-16** — Plan created from the exploration of 2026-11-15. The user chose four separate
    features for the proposed improvements; this is the first.
- **2026-11-16** — Rooms from the search card removed: the acquisition side corrected the
    one-room extraction the same day, and its replay fills history. The feature is now additive
    and needs no backfill.
- **2026-11-16** — Review approved with three corrections folded in: a total order of sightings
    (M1); the table joins `SILVER_TABLES` and the run reads Silver once before the event and
    price rules (M2); the declared previous price comes from the period's first sighting of
    either kind (M3). Implementation starts after FEATURE-013 is merged.
- **2026-11-16** — Task 3.2: the image of merge commit `9be095f` was deployed, and the first
    run wrote the table: 27,582 price periods of 23,169 listings, every count equal to the same
    rule in Athena. See the [implementation note](implementations/FEATURE-009-listing-price-history.md).
    Feature complete.
