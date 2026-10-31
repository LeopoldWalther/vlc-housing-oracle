# Silver quality exploration

Read-only Athena queries that measure how trustworthy Silver is (FEATURE-005). Each file answers
one quality dimension, returns aggregates only, and is pinned to one set of Silver snapshots, so a
rerun returns the same numbers. The findings and the rules they lead to are in
[`docs/data-quality/`](../../docs/data-quality/).

| File | Dimension |
| --- | --- |
| `01_completeness.sql` | Non-null rate of every listing attribute, listings without a detail record, empty cards |
| `02_plausibility.sql` | Value distributions and rule candidates for implausible values |
| `03_location.sql` | Coordinate precision and placement, shared points, district consistency |
| `04_duplicates.sql` | The same property under several listing ids, in tiers |
| `05_temporal.sql` | Sweep rhythm, missed listings, new listings, price changes, lifetimes |
| `06_redaction.sql` | What redaction `r1` leaves behind in `listing_text` |

## Privacy rule

This repository is public. A committed query's final `SELECT` returns counts, rates or
percentiles only, never a listing id, a single listing's coordinates or price, or a text excerpt.
A drill-down that returns rows (the sample for the manual duplicate check, a look at one outlier)
is written and run privately and never committed. Its results stay in the private bucket's Athena
result prefix.

## Running a file

In the Athena console: workgroup `primary`, database `vlc_housing_silver`, paste the file, run.
From a shell:

```bash
id=$(aws athena start-query-execution --work-group primary \
    --query-execution-context Database=vlc_housing_silver \
    --query-string "$(cat analysis/silver-quality/01_completeness.sql)" \
    --query QueryExecutionId --output text)
aws athena get-query-execution --query-execution-id "$id" --query QueryExecution.Status.State
aws athena get-query-results --query-execution-id "$id" --output text
```

Every file returns one result in long format: `metric`, `operation`, `segment`, `value`.
`segment` is a property type, a percentile, a class or `all`, depending on the metric.

## The snapshot set

Each file starts with one CTE per table it reads, pinned with `FOR VERSION AS OF`. Trino has no
variables, so this block is the file's constant block, followed by a `params` CTE with its
thresholds. One measurement round uses one snapshot set in every file. Take it while no Silver
task runs (`aws ecs list-tasks --cluster vlc-housing-silver` lists none), table by table:

```sql
SELECT snapshot_id FROM "listing$snapshots" ORDER BY committed_at DESC LIMIT 1
```

and replace the ids in every file's block. The findings document names the set it reports on.

## Definitions

**Duplicate tiers**, each a pair of distinct listing ids:

| Tier | Key |
| --- | --- |
| A0 | Same `operation`, `municipality`, `sqm_built`, `rooms`, `bathrooms` |
| A | A0 and equal `price_eur`. The only tier reported as clusters, because it is an exact key |
| A±10 / A±5 | A0 and asking prices within 10% / 5% of the lower one |
| B | A±5 and within the distance threshold (0.003° latitude and 0.004° longitude, about 300 m) |
| C | B and an equal floor; a missing floor never matches |

Distance and tolerance pairs are not transitive, so tiers other than A report pairs and the
listings in at least one pair, not clusters. A pair is **concurrent** when the two listings'
observation spans (`first_seen` to `last_seen`) overlap, and otherwise **sequential**: a
re-listing.

**Time windows.** A collection sweep spans about a week: the scraper runs every three hours until
every area is collected.

- `RAMP_UP_END` is the day every area had been walked at least once (2026-09-30). A listing first
  seen on or before it was found by the first sweep, not published then. A listing counts as new
  only when its `first_seen` is strictly later, or later than its own area's first walk if that
  area was added afterwards.
- `CENSOR_START` is the last collection day minus seven days. A listing last seen on or after it
  may still be online, so it never counts as vanished.
- A listing is missed only when a sweep walked its area without seeing it, never because the
  calendar has a gap.

Sequential duplicates, new listings, price changes and lifetimes use these windows.
Completeness, plausibility, location and concurrent duplicates use every listing.
