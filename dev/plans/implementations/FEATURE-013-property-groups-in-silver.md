# FEATURE-013 — Property groups move from Gold to Silver: implementation note

**Plan:** [FEATURE-013](../FEATURE-013-property-groups-in-silver.md) · **Review:**
[REVIEW-FEATURE-013](../../reviews/REVIEW-FEATURE-013.md) · **Recorded:** 2026-11-16

Counts only; no value of real data appears in this note.

## Phases 1 to 3.1

Tasks 1.1–3.1 reached `main` through the final PR. FEATURE-009 and FEATURE-010 followed on the
same day, so the first deployment after this feature carried all three (image of `9be095f`).

## Task 3.2 — deployment, first run, comparison and the Gold object

- **Image:** the GitHub Actions build of `9be095f`, set as `silver_image` in the operator's local
  tfvars. `terraform apply` added a new task definition revision and the alarm of FEATURE-010,
  and changed the consumer Lambda's reference to the revision; nothing else changed.
- **First run:** the task of 18:02 UTC on 2026-11-16 exited 0 and wrote
  `listing_property_group` (snapshot `8496779096095638971`). It processed a landing commit
  without rows; no backfill.
- **Duration,** from log stream creation to the last log line: 29 s, against 30 s for a run
  without rows in [FEATURE-008](FEATURE-008-listing-quality.md). A run with rows was not
  measured: every landing commit since 2026-11-15 18:01 UTC holds no rows.

## Comparison with the rule of the commit before the merge

A one-off script outside the repository ran from a work tree at `5e03f74`, the first parent of
the merge, which still holds `src/gold`. It applied that commit's `LISTING_COLUMNS`,
`duplicate_pairs` and `property_group_rows` in memory to the current `listing` snapshot (of
2026-11-15) and compared the result with the Silver table, whose snapshot was committed after
it. Nothing was written; the work tree was removed.

| Count | Rule before the merge | Silver | Gold build of 2026-11-14 |
| --- | --- | --- | --- |
| Rows | 23,159 | 23,159 | 21,840 |
| Groups of two or more | 1,969 | 1,969 | 1,832 |
| Listings in such groups | 5,229 | 5,229 | 4,849 |
| Incomplete groups | 194 | 194 | 187 |
| Largest group | 23 | 23 | — |

Listings on one side only: 0. Listings whose `property_group_id`, `group_size` or
`group_is_complete` differ: 0.

## The Gold object

With the user's approval of 2026-11-16, the Gold object `listing_property_group/part-0.parquet`
and its empty folder marker were deleted from the versioned Gold prefix. No other object exists
under that prefix. The file can be rebuilt with the command of `5e03f74`.
