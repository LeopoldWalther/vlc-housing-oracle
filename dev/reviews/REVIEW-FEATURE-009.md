# Review — FEATURE-009: Listing price history

**Reviewer:** `reviewer` · **Date:** 2026-11-16 · **Plan:** [FEATURE-009](../plans/FEATURE-009-listing-price-history.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible and additive: one statement over Silver's own `observation` and
`listing_detail`, written on the path `listing_event` uses. Three corrections were needed, and
all follow from the repository:

- the order of sightings was not total, and the synthetic landing holds sightings at one instant
    with different prices;
- the plan did not say how the table enters the run and its tests, and the recompute from Silver
    has an order hazard;
- the declared previous price was defined for cards only, while a period can start with a detail
    record.

They are folded into the plan. Implementation starts after FEATURE-013 is merged.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-009-listing-price-history.md` (🔵 Planned,
    registered); FEATURE-004 and FEATURE-008 are 🟢 Complete. FEATURE-013 is implemented on its
    integration branch and not merged; this review read the code of that branch.
- **Code, tests, data, and configuration:**
    - `src/silver/sql/observation.sql`: Silver's `observation` holds one row per listing and
        day, with `price_eur`, `prev_price_eur`, `observed_at` and `is_empty_card`.
    - `src/silver/sql/newest_sighting.sql`: `listing_detail` holds one row per source, area,
        listing and `observed_at`, so one listing can have two detail records at one instant.
    - `src/silver/rebuild.py`:
        - `SILVER_TABLES` drives `build_tables`; `listing_event` is in it and is computed there
            from the session's tables, then recomputed from Silver by `_events_from_silver` and
            written before the ingest log. A statement added to the tuple is covered by the
            table enumerations of `tests/test_silver_incremental.py` and
            `tests/test_silver_rebuild.py` without further change.
        - `listing_events()` replaces the temporary tables `silver_walk` and
            `silver_observation` with the event columns. `_silver_columns` falls back to those
            temporary tables while a catalog table does not exist yet.
    - `src/synthetic/landing.py`:
        - `syn-tied` (line 237) has two detail records at one instant with 310,000 and 305,000.
        - `syn-repriced` (line 222) has cards and detail records at the same instants, the
            detail records at 260,000 and then 245,000: a reduction already exists.
        - No listing returns to an earlier price.
        - Scenarios are registered in one list in `generate_landing_v3`.
- **Evidence reviewed:**
    - Athena, counts only, Silver of 2026-11-16: 143,040 distinct instants of priced sightings
        per listing, cards and detail records together. None holds two sightings, so on real data
        the order by `observed_at` alone is already total. The tie rule decides nothing there;
        it makes the synthetic tests deterministic and the table safe against a future tie.
    - The architect's counts of 2026-11-15 (4,389 changes on 3,651 listings) were taken from
        cards alone. The table also reads detail records, so its counts will differ; task 3.2
        compares with the same rule in Athena, not with these numbers.
- **Unverified assumptions:**
    - The recompute adds no noticeable time. Owner: operator. Verification point: task 3.2.
        `listing_event` already reads all of `observation` in every run.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | One table; `listing` unchanged |
| Repository and dependency assumptions match reality | Pass | After M2 |
| Supporting evidence is reproducible and sufficient | Pass | Tie count above |
| Design is the simplest adequate option | Pass | One statement, the `listing_event` path |
| TDD slices and validation commands are executable | Pass | After M1 and M3 |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | After M1; observed and declared prices stay separate |
| Operations, recovery, migration, and cost are addressed | Pass | Additive; $0; no backfill |

## Strengths

- Observed periods and the portal's declared previous price are separate columns.
- `is_current` is defined as "last price seen" and leaves presence to `listing_event`.
- No existing table or column changes, so no `transformation_version` bump and no backfill.

## Findings

### 🟡 M1 — The order of sightings was not total

- **Problem:** Periods were ordered by `observed_at` only.
- **Evidence:** `syn-tied` has two detail records at one instant with different prices;
    `syn-repriced` has a card and a detail record at one instant with different prices.
- **Impact:** Which price is current would depend on the scan order, and "incremental equals
    backfill" could fail at random.
- **Recommendation:** Order by `observed_at`, then cards before detail records (the detail page
    is fetched after the card that led to it), then `price_eur`, then `prev_price_eur` with
    nulls last. Rows equal in all four are interchangeable for every output column.
- **Disposition required:** fixed in the feature plan and in task 1.1.

### 🟡 M2 — How the table enters the run was left open

- **Problem:** "Handled like `listing_event`" named neither `SILVER_TABLES` nor the order of the
    reads from Silver.
- **Evidence:** `listing_events()` narrows `silver_observation` to the event columns. A later
    fallback read of `price_eur` from that temporary table would fail on a catalog that has no
    `observation` table yet.
- **Impact:** The Implementer would choose the wiring, and one choice fails on a first run.
- **Recommendation:** `listing_price` joins `SILVER_TABLES` after `listing_event`. The run reads
    `walk`, `observation` (the union of the event and price columns) and `listing_detail` from
    Silver once, before either rule runs, and passes each rule its columns.
- **Disposition required:** fixed in the feature plan and in task 2.1.

### 🟡 M3 — The declared previous price of a period that starts with a detail record

- **Problem:** The column was defined as "the card's `prev_price_eur` at the period's first
    sighting".
- **Evidence:** `listing_detail` carries `prev_price_eur` too, and a listing can have a detail
    record and no priced card.
- **Impact:** A null where the portal did declare a previous price.
- **Recommendation:** The column is the `prev_price_eur` of the period's first sighting,
    whichever kind it is.
- **Disposition required:** fixed in the feature plan and in task 1.2.

### 🟢 L1 — The reduction scenario exists

- **Evidence:** `syn-repriced` goes from 260,000 to 245,000.
- **Suggestion:** Task 2.2 adds only the listing that returns to its first price. Applied.
- **Benefit:** A smaller generator change and fewer pinned expectations to update.

### 🟢 L2 — One more snapshot per run

- **Evidence:** As for `listing_event`, `listing_quality` and `listing_property_group`.
- **Suggestion:** Covered by the pending decision on snapshot expiry.
- **Benefit:** None beyond the record.

## Alternatives considered

- **Price periods from cards only** — simpler statement. Trade-off: a listing with a detail
    record and no priced card has no price at all, and the detail price is a sighting like any
    other. Verdict: stick with the plan.
- **Replace `listing.price_eur` by the current price** — one place for the price. Trade-off: a
    changed value in an existing column needs a new `transformation_version` and a backfill.
    Verdict: out of scope, as planned.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| A consumer reads `listing.price_eur` and misses the current price | Med | Med | The runbook states which table holds the current price | Consumer; documentation |
| Weekly sightings hide short-lived prices | High | Low | Documented: a period is what was observed | — |
| The new synthetic listing pairs with an existing one under rule `pg1` | Low | Low | It gets attributes of its own; the property-group test would fail | Implementer; task 2.2 |

## Effort check

- **Plan estimate:** S (~6h)
- **Reviewer estimate:** S (~6h) — confidence High
- **Why it differs / hidden complexity:** none; the path exists.

## Reuse & conflicts

- **Reuse:** `listing_events()` and `_events_from_silver` as the pattern; `_silver_columns`;
    `write_silver_tables`; the predates test of `tests/test_silver_incremental.py`.
- **Conflict / coordinate with:** FEATURE-013 changes `process_landing`; this feature starts from
    `main` after its merge. FEATURE-010 to FEATURE-012 follow.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-009-technical-plan.yaml`
- **Tasks:** 6; dependency graph and critical path verified yes
- **TDD contract:** RED failures and commands verified yes
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** M1, M2 and M3 fixed in the feature plan and the technical plan
- **Optional:** L1 applied; L2 left as it is
- **Accepted residual risk:** the unmeasured task duration, measured in task 3.2
- **Technical plan:** emitted

## Next step

Run `/implementer FEATURE-009` after FEATURE-013 is merged to `main`.
