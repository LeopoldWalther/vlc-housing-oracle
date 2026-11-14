# FEATURE-007 — Property groups: implementation note

**Plan:** [FEATURE-007](../FEATURE-007-property-groups.md) · **Review:**
[REVIEW-FEATURE-007](../../reviews/REVIEW-FEATURE-007.md) · **Recorded:** 2026-11-14

Counts only; no value of real data appears in this note.

## Phases 1 to 4.1

Tasks 1.1–4.1 reached `main` through the final PR (merge commit `df3f7e2`). Every task's test
failed first and passes; the full suite, ruff, the leak guard and the workflow validator passed on
the integration branch after `main` (with FEATURE-002) was merged into it.

## Task 4.2 — first real build

- **Command:** `python -m src.gold.property_group --catalog silver --out <lake-bucket>/gold/v1`,
  run on 2026-11-14 from a checkout of `main` with the operator's credentials and the Glue catalog
  configured through `PYICEBERG_CATALOG__SILVER__*`. It exited 0 and wrote
  `gold/v1/listing_property_group/part-0.parquet`, the first object under a Gold prefix.
- **Snapshot:** Silver `listing` snapshot `2393135892202708854`, committed 2026-11-08, the
  current one at build time. Silver has gained no new listing since, so the build saw the same
  state as the architect's evidence.

| Count | Architect's evidence (2026-11-14) | First build |
| --- | --- | --- |
| Listings with a detail record (rows) | 21,840 | 21,840 |
| Groups of two or more | 1,832 | 1,832 |
| Listings in a group | 4,849 | 4,849 (1,832 + 3,017 surplus) |
| Incomplete groups | 187 | 187 |
| Groups above five listings | — | 63 |
| Largest group | 19 | 19 |

Nothing decreased; every count equals the evidence.

## Checks on the written file

Read back privately, counts only:

- 21,840 rows, one per `(source, source_listing_id)`, with the eight contract columns; all rows
  carry the one recorded snapshot id.
- At that snapshot Silver `listing` holds 21,850 listings, 21,840 of them with a detail record:
  the row count equals the listings with a detail record.
- 18,823 distinct `property_group_id` = 1,832 groups + 16,991 single listings.
