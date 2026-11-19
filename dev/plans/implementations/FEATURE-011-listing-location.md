# FEATURE-011 — Listing location from coordinates: implementation note

**Plan:** [FEATURE-011](../FEATURE-011-listing-location.md) · **Review:**
[REVIEW-FEATURE-011](../../reviews/REVIEW-FEATURE-011.md) · **Recorded:** 2026-11-19

Counts only; no value of real data and no boundary appears in this note.

## Phases 1 to 3.3

Tasks 1.1–3.3 reached `main` through the final PR (merge commit `09be771`).

## Task 3.4 — first vintage, deployment and first run

- **Vintage:** `2026-01-01`, the statistics office's 2026 edition, retrieved on 2026-11-16 and
  converted for province 46 by `python -m src.reference.census_sections`: 1,841 sections, as
  the plan expected. The manifest holds the download address, the licence with its attribution
  line, and the archive's SHA-256
  `3132a7ea07eaf6183b59f66bfbbf7d379662e9cf2759284358e88cedcc035ed2`.
- **Upload:** on 2026-11-18 the two files went to `reference/v1/census_sections/vintage=2026-01-01/`
  of the lake with `--if-none-match '*'`. The prefix `reference/` was empty before.
- **Image and deployment:** the GitHub Actions build of `09be771`. `terraform apply` on
  2026-11-18 21:38 UTC added the statement `ReadReferenceV1` and the prefix `reference/v1/*` to
  the task role's policy, and created a task definition revision with the new image and the
  argument `--census-sections`; the consumer Lambda's reference to the revision changed with
  it. Nothing else changed.
- **First run, by a backfill: a deviation from the plan.** The plan expected the next landing
  commit to start the first run. The acquisition side's gate has rested since the evening of
  2026-11-18 (last commit 18:02 UTC) and announces the next commit for 2026-11-22, and a run
  with no pending commit writes no table. With the user's approval of 2026-11-19 the first run
  was the runbook's manual backfill, with `--census-sections` added to its command: event
  source mapping disabled, one task, mapping enabled again. The task exited 0, processed 359
  commits and 23,169 listings, wrote every table and printed
  `listing_location: snapshot 8107737977967587215` and `unknown feature patterns: 0`.
- **Duration:** 4 min 6 s from the container's start to its stop (06:08:56 to 06:13:02 UTC on
  2026-11-19), for the whole history. This cannot be compared with the 29 s the notes of
  FEATURE-009, 010 and 013 record for an incremental run; the backfill of FEATURE-006 took
  about three minutes on a shorter history. An incremental run with the argument was not
  measured: the first one comes with the commit of 2026-11-22. The review measured the join
  alone at 0.1 s.

## Counts in Athena

Aggregates over `listing_location` and `listing` of the same run, beside what the review
measured privately on Silver of 2026-11-16 before the table existed.

| Count | First run | Review |
| --- | --- | --- |
| Listings in `listing` | 23,169 | — |
| Listings with a detail record | 23,159 | 23,159 |
| Rows in `listing_location` | 23,159 | — |
| Listings with a detail record and no row, or a row and no detail record | 0 | — |
| Rows with coordinates | 22,974 | 22,974 |
| Rows with a section (and so a district and a municipality) | 22,961 | 22,961 |
| Rows with coordinates and no section | 13 | 13 |
| Rows with a section and no coordinates | 0 | — |
| Sections in use | 1,100 | 1,100 |
| Districts in use | 87 | 87 |
| Municipalities in use | 50 | 50 |
| Rows whose municipality differs from the one most listings of their portal municipality have | 515 | 515 |
| Rows with a null `reference_vintage` | 0 | — |
| Vintages on the rows | 1 (`2026-01-01`) | — |
| Transformation versions on the rows | 1 (`silver-v1`) | — |

Every count equals the review's; Silver has held no new rows since. The 13 listings with
coordinates and no section are kept with null codes, as the rule says: no evidence, not
"outside the province".

## Follow-up

The runbook's manual backfill command does not name `--census-sections`. A backfill started
exactly as written leaves `listing_location` as it was, which is harmless but stale; the command
used here added the argument. The runbook is corrected separately.
