# FEATURE-006 — Redaction r2: implementation note

**Plan:** [FEATURE-006](../FEATURE-006-redaction-r2.md) · **Review:**
[REVIEW-FEATURE-006](../../reviews/REVIEW-FEATURE-006.md) · **Recorded:** 2026-11-05

Counts only; no value of real data appears in this note.

## Phase 1 — r2 patterns

Tasks 1.1–1.3 reached `main` through the final PR (merge commit `e4b7643`). The redaction tests
failed first and pass; the full suite, ruff, the leak guard and the workflow validator passed on
the integration branch after `main` was merged into it.

## Task 2.1 — deploy and backfill

- **Image:** the GitHub Actions build of `e4b7643`, set as `silver_image` in the operator's local
  tfvars. `terraform apply` changed only what follows from the image: a new task definition
  revision and the consumer Lambda's reference to it.
- **Backfill:** run on 2026-11-05 with the event source mapping disabled, as documented in
  `docs/silver-rebuild.md`; the task exited 0 after about three minutes, and the mapping was
  enabled again afterwards.
- **Equality check:** landing v3 was synced right after the task stopped and backfilled locally
  into a SQLite catalog. Every table matched:

| Table | Rows (AWS = local) |
| --- | --- |
| `landing_ingest` | 253 (same commits) |
| `listing` | 20,518 |
| `listing_detail` | 20,594 |
| `listing_text` | 20,508 |
| `observation` | 91,338 |
| `walk` | 2,216 |

Both `listing_text` snapshot summaries record `redaction_version = r2`.

## Task 2.2 — residue rerun

`analysis/silver-quality/06_redaction.sql` (FEATURE-005), run privately with the snapshot pin
replaced by the backfill's `listing_text` snapshot `5954108916071801353` (committed 2026-11-05),
the newest one; no Silver task was running. Values are the number of listings whose redacted
text matches, by segment. Title, location and features are 0 for every metric, so only the
description is shown, beside the r1 scan of 2026-10-31 that motivated r2:

| Metric | r1 (2026-10-31) | r2 (2026-11-05) |
| --- | --- | --- |
| `r1_phone_residue` | 0 | 0 |
| `phone_glued_country_code` | 1 | **0** |
| `phone_glued_to_letter` | 0 | 0 |
| `phone_inside_longer_number` | 11 | 11 |
| `name_not_capitalised` | 3 | **2** |
| `email` | 0 | 0 |
| `web_address` / `web_address_spaced` | see FEATURE-005 | 3 / 21 |
| `redacted_phone` | — | 634 |
| `redacted_name` | — | 55 |
| descriptions | 19,038 | 20,508 |

The two remaining `name_not_capitalised` matches are the lower-case words the plan expected to
survive. `SELECT count_if(redaction_version <> 'r2'), count(*) FROM listing_text` on the same
snapshot returns 0 of 20,508.

## Remaining

- Iceberg snapshots older than the backfill still hold r1 text in the private lake bucket. Whether
  to expire them is the user's decision after FEATURE-005 closes, because its queries are pinned
  to the 2026-10-31 snapshots.
