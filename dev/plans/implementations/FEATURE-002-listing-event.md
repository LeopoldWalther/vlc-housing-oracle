# FEATURE-002 — Listing events: implementation note

**Plan:** [FEATURE-002](../FEATURE-002-listing-event.md) · **Review:**
[REVIEW-FEATURE-002](../../reviews/REVIEW-FEATURE-002.md) · **Recorded:** 2026-11-14

Counts only; no value of real data appears in this note.

## Phases 1 to 3.1

Tasks 1.1–3.1 reached `main` through the final PR (merge commit `452146e`). The full suite (166
tests), ruff, the leak guard and the workflow validator passed on the integration branch.

Two expected RED failures of task 1.2 did not occur: the replay case and the null
`complete`/`is_replay` case already passed, because task 1.1's evidence filter excluded replays.
They stay as guards. Task 2.1 added one case the plan did not name: when Silver has no `walk`
table yet (the first commits held no walk day), the rule reads an empty table instead of failing.

## Task 3.2 — deployment and first real run

- **Image:** the GitHub Actions build of `452146e`, set as `silver_image` in the operator's local
  tfvars. `terraform apply` changed only what follows from the image: a new task definition
  revision and the consumer Lambda's reference to it.
- **First run:** the task started by the 12:00 UTC landing commit on 2026-11-14 exited 0, processed
  one pending run and created `listing_event` with snapshot `4693251489635103724` (committed
  2026-11-14 12:02 UTC). No backfill.
- **Duration:** 21 s from log stream creation to the last log line, against 22–24 s for the eight
  tasks before the change, which processed the same kind of run. The whole-table recompute adds no
  measurable time.

## Comparison with the rule in Athena

The rule was written independently in Athena SQL (window functions with `IGNORE NULLS`, the
evidence filter with the UTC dates of start and end, the first evidence day per gap) over the
current Silver `walk` and `observation`, and compared with `listing_event` at the snapshot above.
No Silver task ran in between.

| Event type | `listing_event` | Athena rule |
| --- | --- | --- |
| `disappeared` | 8,056 | 8,056 |
| `reappeared` | 396 | 396 |

Rows of the table missing from the rule and rows of the rule missing from the table, over all
seven columns: 0 and 0. The 8,452 rows hold 8,452 distinct keys
`(source, source_listing_id, event_type, event_date)` over 7,956 listings. The counts equal the
architect's evidence of 2026-11-14 (8,056 and 396).
