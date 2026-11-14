# FEATURE-002 — Listing events: disappeared and reappeared

**Status:** 🔵 Planned · **Effort:** M (~8h) · **Priority:** High
**Branch root:** `feature/listing-event` · **Created:** 2026-11-14 · **Updated:** 2026-11-14

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-002.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-002-technical-plan.yaml`.

## Objective

Give Silver a `listing_event` table that states, for every listing, when a complete walk of its
area first failed to find it (`disappeared`) and when it was seen again afterwards
(`reappeared`). Time on market, liquidity and market dynamics all need this, and today every
consumer would have to re-derive it from `observation` and `walk` with its own, slightly
different rule.

## Context

- **Current state:**
    - Silver holds `walk` (one row per run report, with `complete` and `is_replay`),
        `observation` (one row per listing and day, with the `area` and `run_id` of the kept
        sighting and `is_empty_card`), `listing_detail`, `listing`, `listing_text` and the ingest
        log ([runbook](../../docs/silver-rebuild.md)).
    - `listing` carries `first_seen`, `last_seen` and `days_observed`, but nothing says whether a
        listing that was not seen lately was looked for.
    - `process_landing` in [`src/silver/rebuild.py`](../../src/silver/rebuild.py) replaces the
        day partitions a run touches, recomputes the affected listings from Silver's history and
        writes the ingest log last.
    - The number FEATURE-002 has been reserved for this table since FEATURE-001 (decided by the
        user on 2026-10-14), and FEATURE-004 lists it as unblocked.
- **Problem:** absence is only meaningful against a walk. A listing whose `last_seen` is two
    weeks old may be gone, or its area may not have been walked completely since. The data-quality
    exploration (FEATURE-005) wrote that rule once as an ad-hoc query; it is not a tested,
    versioned part of Silver.
- **Constraints:**
    - A disappearance is an observation-derived state, never proof of a sale or a rental
        (`CLAUDE.md`). The table states what the walks saw and infers nothing beyond it.
    - A replay is never evidence that a listing disappeared (landing contract v1 and v3).
    - A collection sweep takes about a week, so the day a disappearance is noticed lies days
        after the listing actually left.
    - Every Silver table must give the same rows through any order of landing commits and
        through a backfill.

## Scope

- **In scope:**
    - one new Silver table, `listing_event`, computed by one DuckDB statement from Silver's own
        `walk` and `observation`;
    - its maintenance in both modes of `python -m src.silver.rebuild`;
    - the runbook entry and one operator run that creates the table in AWS.
- **Out of scope:**
    - other event types (price change, first seen, attribute change): `first_seen` is already on
        `listing`, and price history is readable from `observation`;
    - a "current state" column on `listing`: a consumer takes a listing's latest event;
    - any label on top of the facts: "sold", "rented", "flicker", a minimum number of missed
        walks. Those are Gold's inferred labels;
    - linking a disappearance to a re-listing under a new id: that needs the property groups of
        the quality follow-up feature;
    - events per collection run instead of per day (see Design);
    - snapshot expiry, and any infrastructure change.
- **Users / consumers:** the Gold features for liquidity and market dynamics, and the operator.

## Dependencies

- **Needs:** FEATURE-004 (incremental Silver from landing v3) — complete.
- **Unblocks:** time on market and liquidity in Gold; the sequential-duplicate (re-listing) part
    of the quality follow-up feature.

## Open assumptions

The architect ran read-only aggregate queries in Athena on 2026-11-14 against the current Silver
tables (21,850 listings with a sighting, 105,521 listing-days, 2,382 walk rows, last walk on
2026-11-08) and one throwaway DuckDB timing script. Only counts were inspected; nothing was saved.

- **Assumption:** a listing has one area whose walks can speak for it. — **Evidence:** 45 of
    21,850 listings were ever seen as a real card in more than one area (0.2%); 16 were only ever
    seen as empty padding cards and get no area. Every observation's area has walks. —
    **If wrong:** a listing that alternates between two areas gets a `disappeared` and a
    `reappeared` it did not earn. At 0.2% this is accepted, and the events of such a listing
    still state true absences from one area's walk.
- **Assumption:** the walk's day is the right unit of evidence. — **Evidence:** of 918 complete,
    live walks none crosses midnight and none is dated differently from its run's start day; 71 of
    689 area-days hold more than one complete walk. — **If wrong:** a walk that crosses midnight
    could miss a listing whose card is dated the next day. The rule therefore excludes such walks
    as evidence; today that excludes nothing.
- **Assumption:** events are frequent enough to matter and returns are rare enough to be
    believable. — **Evidence:** the rule below, written in Athena, yields 8,056 `disappeared`
    events over 7,956 listings (36% of all listings) and 396 `reappeared` events (4.9% of the
    disappearances). The median disappearance is noticed 7 days after the last sighting (90th
    percentile 13). Of the 396 returns, 259 follow a single missed walk day and 137 two or more;
    217 come more than 16 days after the last sighting. — **If wrong:** a much higher return rate
    after one missed walk would point at walks that are marked complete without being so, which
    is a finding for the acquisition side, not a reason to change this table.
- **Assumption:** recomputing the whole table in every run stays cheap. — **Evidence:** the
    statement below ran in 1.1 s in DuckDB 1.5.6 on 2.1 million generated listing-days and 34,500
    walks, about two years of the current volume, in a shape where every sighting produces an
    event. — **If wrong:** the table is switched to recomputing only the listings of the areas a
    run walked, with the existing key upsert; the rows stay the same.
- **Assumption:** a first run creates the table without a backfill and without new permissions.
    — **Evidence:** `_table_for` in `src/silver/sink.py` creates a missing table from the Arrow
    schema in either mode, and the task role's Glue statement covers every table of the Silver
    database (`infra/silver/ecs.tf`). — **If wrong:** the operator run (3.2) fails at its first
    step with an access error and needs a one-line policy change, planned then.

## Design

- **Primary approach:** one more SQL statement, `src/silver/sql/listing_event.sql`, over Silver's
    `walk` and `observation`. In every run, after the day partitions are replaced, the whole
    table is recomputed from Silver's full `walk` and the needed columns of `observation`, and
    overwritten in one commit, before the ingest log.

    This is a deliberate exception to "replace day partitions": the table has no day a run could
    own, because an event's day is decided by rows of other days. It stays a replacement of a
    whole unit, never an append, and it is not a backfill, because it reads Silver and not
    landing (review M2).

    Whole-table recomputation is chosen over an upsert of affected listings because an event
    depends on rows far from the days a run touches: a walk of an area today can end the gap of
    any listing last seen there weeks ago, and a late run that fills an old day can retract an
    event. Recomputing everything makes "incremental equals backfill" true by construction, and
    the measured cost is about a second for two years of data.

- **The rule.** All terms are per `source`.
    - A **sighting** is a row of `observation`: the listing was seen that day. An empty card
        counts as seen.
    - A sighting's **home area** is the `area` of the listing's latest sighting up to that day
        that is not an empty card. A listing never seen as a real card has none and gets no
        event.
    - An **evidence day** of an area is a day with at least one walk of that area that is
        `complete`, is not a replay (`is_replay` false, never null), and whose run starts and
        ends on the same UTC day.
    - For each sighting, let the gap run from its day to the listing's next sighting, or without
        end if there is none. The listing **disappeared** on the first evidence day of the
        sighting's home area strictly inside that gap. A gap without such a day yields nothing:
        nobody looked.
    - If the gap ends in a next sighting and a `disappeared` event lies in it, the listing
        **reappeared** on that next sighting's day.

    So each gap yields at most one `disappeared` and at most one `reappeared`; later evidence
    days in the same gap add nothing.

- **The table.** `listing_event`, unpartitioned, one row per event:

    | Column | Type | Meaning |
    | --- | --- | --- |
    | `source` | `VARCHAR` | As everywhere in Silver |
    | `source_listing_id` | `VARCHAR` | The listing |
    | `event_type` | `VARCHAR` | `disappeared` or `reappeared` |
    | `event_date` | `DATE` | The evidence day, or the day of the sighting that ended the gap |
    | `area` | `VARCHAR` | `disappeared`: the home area whose walk is the evidence. `reappeared`: the area of the sighting |
    | `run_id` | `VARCHAR` | `disappeared`: the earliest complete live run of that area on that day, by `run_started_at`. `reappeared`: the sighting's run |
    | `last_seen_date` | `DATE` | The sighting that opens the gap. The listing left between this day and `event_date` |
    | `transformation_version` | `VARCHAR` | `silver-v1` |

    The key `(source, source_listing_id, event_type, event_date)` is unique. Rows are ordered by
    listing, date and type, so two computations give identical files' rows.

- **Boundaries and data flow:** landing → Silver day tables (unchanged) → `listing_event` from
    Silver itself. The statement reads `silver_walk` and `silver_observation`, the names the
    existing statements use. One public function, `listing_events`, takes Silver's walk and
    observation rows as Arrow tables and returns the events; the rule's tests call it with a few
    hand-written rows, and `process_landing` calls it with the rows it scans from the catalog
    after the day tables are committed. From task 2.1 on the table is also listed in
    `SILVER_TABLES`, so `build_tables` computes it over every run as the oracle of the equality
    tests. No statement reads landing for it, and nothing outside `src/silver/` changes.

- **Contracts and invariants:**
    - **Provenance:** every event names its listing, the day and run that evidence it, and the
        transformation version; the run's ingestion time is in `landing_ingest`.
    - **Time:** `event_date` is when the absence was noticed, not when the listing left. The
        truth lies in `(last_seen_date, event_date]`. Dates are the collector's UTC days.
    - **Retraction:** a late run can add a sighting inside a gap or change which walks are
        complete. The next run's table then simply no longer holds the old event; a consumer
        that needs stability pins a snapshot.
    - **Censoring:** a listing with no event after its last sighting is not "online"; it is
        unknown until its area is walked completely again.
    - **Idempotency:** the table is a pure function of Silver's `walk` and `observation`. A
        repeated or interrupted run overwrites it with the same rows.
    - **Version:** existing tables do not change, so `transformation_version` stays `silver-v1`
        and no backfill is needed. A later change of the event rule is a new version and a
        backfill, like any rule change.

- **OOP / SOLID:** pure functions and SQL are sufficient. The feature adds one statement and one
    function that feeds it from the catalog; there is no state, variant or new boundary.
- **Patterns:** None.
- **Deliberately rejected complexity:**
    - **Events per collection run.** ADR 0003 made them possible, but Silver's `observation`
        keeps one sighting per listing and day, so a per-run rule needs a new per-run sighting
        table. With a weekly sweep, a day is far below the resolution of the data, and the same
        area is walked twice on one day in only about a tenth of the area-days.
    - **An upsert of affected listings.** See the primary approach; kept as the fallback if the
        volume assumption fails.
    - **A confirmation threshold** (two missed walks before an event). It would hide the 259
        single-miss returns instead of recording them. A consumer can require a second evidence
        day itself; the facts stay complete.
    - **New synthetic scenarios in the generator.** The generator's filler listings, seen on
        random days under daily complete walks, already produce both event types; the rule's
        own tests use small hand-written landing.

## Approach

Each rule task writes its test first against `listing_events` on a few hand-written rows of
Silver's `walk` and `observation` (only the columns the rule reads), watches it fail, and adds
only the SQL it needs. Results are compared as Arrow rows. The table joins `SILVER_TABLES` only
in task 2.1, so the existing suite stays green after every task (review M1).

### Phase 1 — The rule

- [ ] **1.1 A disappearance.**
    - RED: a listing seen in one area on day 1, and a complete live walk of that area on day 3
        that does not see it. The test expects one `disappeared` row with `event_date` day 3, the
        area, the walk's `run_id`, `last_seen_date` day 1 and the transformation version. It
        fails on the import: `listing_events` does not exist.
    - GREEN: `listing_event.sql` with sightings, evidence days and the first evidence day in a
        gap; `listing_events(connection, walk, observation)` in `src/silver/rebuild.py`.
    - REFACTOR: name the common table expressions after the rule's terms.
- [ ] **1.2 What is not evidence.** One test per case, each expecting no event:
    - an incomplete walk; a replay; a legacy walk whose `complete` and `is_replay` are null;
    - a walk of another area; a walk on the day of the sighting or before it;
    - a walk whose run ends on the next UTC day;
    - a listing only ever seen as an empty card.

    And one expecting that an empty-card sighting closes a gap like any other. GREEN adds the
    filters and the home-area rule.
- [ ] **1.3 A return, and more than one gap.**
    - RED: a listing seen, missed by a complete walk, and seen again expects a `disappeared` and
        a `reappeared` with the right dates, areas and run ids; a listing with two such gaps
        expects four events; a listing whose latest real card moved to a second area expects the
        second area's walks to be its evidence.
    - GREEN: the `reappeared` branch.
- [ ] **1.4 One event per gap, deterministically.**
    - RED: three complete walks inside one gap expect a single `disappeared` on the first; two
        complete walks of the area on that day expect the `run_id` of the run that started
        earlier; rows come back ordered by listing, date and type; no walks and no sightings give
        an empty table with the contract's columns and types.
    - GREEN: the tie-break and the final `ORDER BY`.

### Phase 2 — Maintenance in the pipeline

- [ ] **2.1 The table is written in both modes, and every sequence gives the same events.**
    - RED, all written before any pipeline code:
        - `listing_event` joins `SILVER_TABLES`, so `tests/test_silver_rebuild.py` and
            `tests/test_silver_incremental.py` fail: `process_landing` writes no such table;
        - a late run retracts an event: a landing in which a walk makes a listing disappear, then
            a run that adds a sighting inside that gap; the event is gone and the table equals a
            backfill;
        - tables that predate the feature: process some commits, drop `listing_event`, process
            the rest; the table equals a backfill and no other table's schema changed.
    - GREEN: a function that scans Silver's `walk` and the six needed columns of `observation`
        from the catalog and calls `listing_events`; `process_landing` overwrites the table with
        the result after the listings and before the ingest log, in both modes, and returns its
        snapshot id.
    - REFACTOR: keep the table list and the report in one order.
- [ ] **2.2 The command reports the table.**
    - RED: `tests/test_silver_cli.py` expects a `listing_event: snapshot <id>` line.
    - GREEN: one line in `_report`.

### Phase 3 — Documentation and the first real run

- [ ] **3.1 Runbook and README.** Add the table to "The tables" in `docs/silver-rebuild.md` with
    its rule, the censoring and retraction notes, the whole-table replacement and the example
    output line; update the README status sentence from five to six tables. Validation: the leak
    guard and the workflow validator pass.
- [ ] **3.2 Operator run.** After the merge, with explicit approval: set `silver_image` to the
    new image and apply (a new task definition revision only), let the next landing commit or the
    catch-up run, and confirm from the task log that `listing_event` got a snapshot and how long
    the task took. Then compare, in Athena and as counts only, the table's rows per `event_type`
    with the same rule written in Athena SQL over the `walk` and `observation` snapshots of that
    run: an independent implementation, and no real data leaves the lake (review M3). Record the
    counts and the task duration in `dev/plans/implementations/FEATURE-002-listing-event.md`.

## Files

- **Create:** `src/silver/sql/listing_event.sql` — the rule.
- **Change:** `src/silver/rebuild.py` — `listing_event` in the table list, the function that
    feeds the statement from the catalog, the overwrite in `process_landing`, the report line.
- **Change:** `docs/silver-rebuild.md`, `README.md` — the new table.
- **Tests:** `tests/test_silver_listing_event.py` — the rule (phase 1); the retraction and the
    pre-feature tables (2.1).
- **Tests:** `tests/test_silver_incremental.py`, `tests/test_silver_rebuild.py`,
    `tests/test_silver_cli.py` — the table in the equality, backfill and command tests.
- **Create (3.2):** `dev/plans/implementations/FEATURE-002-listing-event.md` — counts of the
    first real run.

## Test strategy

- **Unit:** the rule on hand-written landing: the happy path, every kind of walk that is not
    evidence, empty cards, a moved home area, several gaps, ties.
- **Contract / fixtures:** the synthetic generator unchanged; the new table's column names and
    types asserted once, like the other tables.
- **Integration:** a local SQLite catalog; a backfill equals the rule over every run; one at a
    time, three at a time and all at once agree.
- **Retries / idempotency / evolution:** an interrupted and repeated run; a late run that
    retracts an event; a first incremental run over tables that predate the feature creates the
    new table and leaves the others untouched.
- **Configured quality checks:**
    - `python -m unittest discover -s tests -t .`
    - `ruff check src tests dev && ruff format --check src tests dev`
    - `python dev/tools/check_leaks.py`
    - `python dev/tools/validate_workflow.py`
- **Manual:** the operator run of 3.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing new crosses the landing contract. The table stays in the
    private lake; published results remain aggregates.
- **Privacy / security:** no free text and no new personal data: listing ids, dates, areas and
    run ids, which Silver already holds. No IAM change.
- **Observability:** one more line in the task log. No new metric or alarm.
- **Failure and recovery:** the table is written before the ingest log, so a failure leaves the
    runs pending and the next run overwrites it. A rollback follows the runbook; the table can
    also be dropped and is recreated whole by the next run.
- **Migration / compatibility:** additive. No existing table, column or version changes, no
    backfill. Until the first run with a pending commit after deployment, the table does not
    exist.

## Estimated monthly cloud cost

$0/month — no resources added or changed.

- **Cost drivers & cheaper alternatives:** each run reads Silver's `walk` and five columns of
    `observation` once more and writes one small file: a few seconds of the Fargate task that
    already runs, and well under 1 MB per run in S3, about 250 MB a month until snapshots are
    expired (under $0.01). Recomputing only affected listings would cut the read, not the bill.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] `listing_event` exists in Silver with the columns above, and every row's key is unique.
- [ ] Every case of phase 1 is a test that failed first: no event from an incomplete walk, a
    replay, a legacy walk, another area's walk, a midnight-crossing run, or for a listing without
    a real card.
- [ ] Any sequence of landing commits and a backfill give the same `listing_event` rows.
- [ ] A late run that fills a gap removes the event, and a repeated run changes nothing.
- [ ] The first real run's counts per event type equal the same rule written in Athena SQL over
    the same Silver snapshots.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak and workflow checks pass
- [ ] The runbook documents the table, its censoring and its retraction behavior
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Decided at review (2026-11-14), open to the user's veto until task 1.1 starts:** the day is
    the unit, and every disappearance is recorded without a confirmation threshold. Choosing the
    run instead adds a per-run sighting table and roughly doubles the effort.
- **Risk:** consumers read `event_date` as the day a listing left. — *Mitigation:* the column
    doc, the runbook and `last_seen_date` on every row state the interval.
- **Risk:** a walk is marked complete although it missed cards, which shows as a
    disappearance and a return a week later. — *Mitigation:* none in Silver, which trusts
    landing's `complete`; the 259 single-miss returns are the measure to watch and to report
    upstream if it grows.
- **Risk:** the whole-table overwrite adds a snapshot per run while no expiry policy exists. —
    *Mitigation:* negligible volume; the expiry decision already pending covers this table too.
- **Risk:** FEATURE-007's plan adds a row to `dev/plans/README.md` in the same working tree. —
    *Mitigation:* a one-line conflict, resolved in whichever is merged second.

## Progress log

- **2026-11-14** — Plan created. Evidence gathered on the current Silver tables (aggregates
    only) and with a DuckDB timing script; both are recorded under the open assumptions.
- **2026-11-14** — Review approved with corrections folded in:
    - M1: the rule is tested through `listing_events` and the table joins `SILVER_TABLES` only
        with the pipeline task, so no task leaves the suite red; the retraction and
        pre-feature-table tests move into that task's RED step;
    - M2: the whole-table replacement is recorded as a deliberate exception, and the first real
        run records the task duration;
    - M3: the first real run is checked against an Athena implementation of the rule instead of
        a local copy of real landing.
