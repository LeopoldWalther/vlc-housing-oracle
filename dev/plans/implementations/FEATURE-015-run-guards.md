# FEATURE-015 — Run guards: implementation note

**Plan:** [FEATURE-015](../FEATURE-015-run-guards.md) · **Review:**
[REVIEW-FEATURE-015](../../reviews/REVIEW-FEATURE-015.md) · **Recorded:** 2026-11-25

Counts and versions only; no row, identifier, bucket or account name of real data appears in this
note.

## Dry run on real landing (task 4.2)

On 2026-11-25 landing v3 was copied from the lake to a directory outside the repository (1,855
objects, about 28 MB) and backfilled from the integration branch into a fresh local SQLite
catalog.

| Check | Result |
|---|---|
| Exit code of the local backfill | 0 |
| Runs processed, each with its four files checked against its commit's row counts and days | 371 |
| Unknown feature patterns | 0 |
| `transformation_version` in the real ingest log | `silver-v1` on all 371 rows |
| `redaction_version` of `listing_text`'s current snapshot | `r2` |
| The code's versions | `silver-v1`, `r2` |

No file was refused, so the stop rule did not apply. The deployed tables carry the code's
versions, so the first incremental run after the deployment passes the guard without a backfill.
The local copy and the local catalog were deleted afterwards.

## Deviation from the technical plan

The refusal line names the cause by its real qualified name, for example
`[cause: _duckdb.InvalidInputException]`; the plan's example wrote `duckdb.IOException`.
