# FEATURE-008 — Listing quality flags: implementation note

**Plan:** [FEATURE-008](../FEATURE-008-listing-quality.md) · **Review:**
[REVIEW-FEATURE-008](../../reviews/REVIEW-FEATURE-008.md) · **Recorded:** 2026-11-15

Counts only; no value of real data appears in this note.

## Phases 1 to 3.1

Tasks 1.1–3.1 reached `main` through the final PR (merge commit `6e16b65`). The full suite (215
tests), ruff, the leak guard and the workflow validator passed on the integration branch.

One deviation from the technical plan's conventions: the haversine distance is written inline in
`listing_quality.sql`, not as a macro, because `build_tables` wraps each statement in
`CREATE OR REPLACE TEMP TABLE … AS` and a statement can therefore hold one query only.
The thresholds reach the statement as `BIGINT` variables, and a per-operation threshold is chosen
with `IF(is_rent, getvariable(…), getvariable(…))`, because `getvariable` needs a constant name.

## Task 3.2 — deployment and first real run

- **Image:** the GitHub Actions build of `6e16b65`, set as `silver_image` in the operator's local
  tfvars. `terraform apply` changed only what follows from the image: a new task definition
  revision and the consumer Lambda's reference to it.
- **First run:** the task started at 18:21 UTC on 2026-11-15 exited 0, processed one pending run
  with one day of rows and created `listing_quality` with snapshot `2963869593048721912`. No
  backfill.
- **Second run:** the task of 21:02 UTC processed a commit without rows. It left `listing`
  untouched and wrote `listing_quality` again (snapshot `4404357454676169637`), as review note L2
  describes.
- **Duration,** from log stream creation to the last log line:
    - a run with rows took 84 s, against 57 s to 77 s for the six such runs earlier that day,
      whose durations rose from run to run with the size of the day they replaced;
    - a run without rows took 30 s, against 29 s for the same kind of run on 2026-11-14.
  The whole-table recompute adds at most a few seconds. The 21 s to 24 s named in the plan were
  runs without rows and are not the baseline for a run with rows.

## Comparison with the rule in Athena

The rules were written independently in Athena SQL over the current Silver `listing`: the same
thresholds, every product as `BIGINT`, and the centre of a municipality as the exact median of
latitude and of longitude (the mean of the two middle values of the sorted values, review M3).
The result was joined to `listing_quality` on `(source, source_listing_id)` at the second
snapshot above. `listing` did not change between the two snapshots, and no Silver task ran during
the comparison.

Rows on either side without a partner: 0. Rows whose twelve flags differ in any flag: 0.

| Flag | sale, table | sale, rule | rent, table | rent, rule | Evidence of 2026-11-15 (sale / rent) |
| --- | --- | --- | --- | --- | --- |
| Listings with a detail record | 17,236 | 17,236 | 5,923 | 5,923 | 17,218 / 5,659 |
| `price_out_of_bounds` | 0 | 0 | 0 | 0 | 0 / 0 |
| `sqm_built_below_min` | 0 | 0 | 0 | 0 | 0 / 0 |
| `sqm_built_above_max` | 5 | 5 | 0 | 0 | 5 / 0 |
| `price_per_sqm_below_min` | 26 | 26 | 3 | 3 | 26 / 3 |
| `price_per_sqm_above_max` | 5 | 5 | 4 | 4 | 5 / 4 |
| `sqm_usable_above_built` | 0 | 0 | 0 | 0 | 0 / 0 |
| `rooms_above_max` | 10 | 10 | 0 | 0 | 10 / 0 |
| `bathrooms_above_rooms` | 9 | 9 | 2 | 2 | 9 / 2 |
| `year_built_out_of_range` | 5 | 5 | 0 | 0 | 5 / 0 |
| `price_drop_above_max` | 0 | 0 | 0 | 0 | 0 / 0 |
| `coordinates_missing` | 139 | 139 | 46 | 46 | 139 / 44 |
| `far_from_municipality` | 135 | 135 | 11 | 11 | 135 / 9 |

Listings with any plausibility flag: 57 sale and 9 rent, as in the architect's evidence. Listings
with any location flag: 274 sale and 57 rent, against 274 and 53. The rent counts moved with the
264 rent listings that arrived between the evidence query and the first run; the evidence also
used an approximate median, which gave the same far counts for sale.
