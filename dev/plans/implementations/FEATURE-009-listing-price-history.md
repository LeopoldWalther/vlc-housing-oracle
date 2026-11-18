# FEATURE-009 — Listing price history: implementation note

**Plan:** [FEATURE-009](../FEATURE-009-listing-price-history.md) · **Review:**
[REVIEW-FEATURE-009](../../reviews/REVIEW-FEATURE-009.md) · **Recorded:** 2026-11-16

Counts only; no value of real data appears in this note.

## Phases 1 to 3.1

Tasks 1.1–3.1 reached `main` through the final PR. The first deployment after this feature
carried FEATURE-010 as well (image of `9be095f`).

## Task 3.2 — deployment and first run

- **Image:** the GitHub Actions build of `9be095f`; see the
  [FEATURE-013 note](FEATURE-013-property-groups-in-silver.md) for what `terraform apply`
  changed.
- **First run:** the task of 18:02 UTC on 2026-11-16 exited 0 and wrote `listing_price`
  (snapshot `5137317975281769796`). It processed a landing commit without rows; no backfill.
  The table is computed from Silver's `observation` and `listing_detail`, which hold every day
  up to 2026-11-15.
- **Duration:** 29 s, the same run as in the FEATURE-013 note, against 30 s for a run without
  rows before these features. A run with rows was not measured: every landing commit since
  2026-11-15 18:01 UTC holds no rows.

## Comparison with the rule in Athena

The rule was written independently in Athena SQL over Silver's `observation` and
`listing_detail` of the same run: the same sightings (search cards that are not empty and
detail records, each with a price), the same total order (`observed_at`, cards before detail
records, `price_eur`, `prev_price_eur` with nulls last), a new period at every change of price,
and the declared previous price of the period's first sighting. Only counts were returned.

| Count | Table | Rule in Athena |
| --- | --- | --- |
| Rows (price periods) | 27,582 | 27,582 |
| Listings | 23,169 | 23,169 |
| Listings with more than one period | 3,665 | 3,665 |
| Rows with `is_current` | 23,169 | 23,169 |
| Periods below the previous price (reductions) | 4,032 | 4,032 |
| Periods above the previous price (increases) | 381 | 381 |
| Periods with a declared previous price | 5,209 | 5,209 |

Rows with `is_current` equal the listings.

## Current price against `listing.price_eur`

Of the 23,159 listings with a price in `listing`, 3,635 (15.7 %) have a current period whose
price differs from it. `listing.price_eur` is the price of the newest detail record, while the
current period also counts the search cards seen after that record.
