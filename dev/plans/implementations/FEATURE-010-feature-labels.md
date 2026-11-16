# FEATURE-010 — Labels from listing features, and a check for unread features: implementation note

**Plan:** [FEATURE-010](../FEATURE-010-feature-labels.md) · **Review:**
[REVIEW-FEATURE-010](../../reviews/REVIEW-FEATURE-010.md) · **Recorded:** 2026-11-16

Counts, and wordings of the public registry only; no value of real data appears in this note.

## Phases 1 to 4.2

Tasks 1.1–4.2 reached `main` through the final PR (merge commit `9be095f`).

## Task 4.3 — deployment and first run

- **Image:** the GitHub Actions build of `9be095f`. `terraform apply` added the alarm
  `unknown_feature_patterns` and a new task definition revision, and changed the consumer
  Lambda's reference to it; nothing else changed.
- **First run:** the task of 18:02 UTC on 2026-11-16 exited 0, wrote `feature_pattern`
  (snapshot `4109280117465858361`) and `listing_feature_label` (snapshot
  `8515601221412426894`), and printed `unknown feature patterns: 0`. It processed a landing
  commit without rows; no backfill.
- **Duration:** 29 s, the same run as for FEATURE-009 and FEATURE-013, against 30 s for a run
  without rows before these features. A run with rows was not measured: every landing commit
  since 2026-11-15 18:01 UTC holds no rows.
- **Alarm:** in the state OK after the run.

## Comparison with the rule in Athena

The normalisation and the registry were written independently in Athena SQL over Silver's
`listing_text` of the same run; the registry entered the queries as SHA-256 digests. Only counts
were returned.

| Count | Tables | Rule in Athena | Expected (plan, 2026-11-16) |
| --- | --- | --- | --- |
| Wordings | 100 | 100 | 100 |
| Unknown wordings | 0 | 0 | 0 |
| Label rows / text rows | 23,159 | 23,159 | — |
| Tenant-occupied | 1,157 | 1,157 | 1,157 |
| Bare ownership | 71 | 71 | 71 |
| Furnished | 4,488 | 4,488 | 4,488 |
| Kitchen only | 834 | 834 | 834 |
| Unfurnished | 391 | 391 | 391 |
| Null booleans | 0 | — | 0 |

Silver has held no new rows since the plan's counts, so the expected values did not move.

## For the acquisition side

Wordings whose meaning no `listing` column carries (target `unread`):

| Wording | Listings |
| --- | --- |
| solo acceso exterior adaptado para personas con movilidad reducida | 1,926 |
| (listed by digest) | 1,364 |
| acceso y vivienda adaptados para personas con movilidad reducida | 895 |
| plaza de garaje por # € adicionales | 562 |
| en trámite | 508 |
| solo interior de la vivienda adaptado para personas con movilidad reducida | 281 |
| plaza de garaje por # €/mes adicionales | 179 |
| no indicado | 163 |
| chimenea | 47 |

Wordings whose target column is missing for some of their listings (21 wordings, 5,987
listings in all). In every case the column is missing for all of the wording's listings:

| Wording | Target | Listings, all with the target missing |
| --- | --- | --- |
| # habitación | `rooms` | 1,877 |
| calefacción individual: bomba de frío/calor | `heating_type` | 1,624 |
| bajo exterior | `floor` | 1,377 |
| sin habitación | `rooms` | 654 |
| bajo interior | `floor` | 136 |
| entreplanta exterior | `floor` | 116 |
| sin baños | `bathrooms` | 106 |
| bajo | `floor` | 27 |
| casa rural | `property_type` | 23 |
| masía | `property_type` | 11 |
| entreplanta interior | `floor` | 9 |
| caserón | `property_type` | 7 |
| planta -# exterior | `floor` | 5 |
| semi-sótano exterior | `floor` | 4 |
| casa terrera | `property_type` | 3 |
| casa rústica | `property_type` | 2 |
| entreplanta | `floor` | 2 |
| sótano exterior | `floor` | 1 |
| cortijo | `property_type` | 1 |
| planta -# interior | `floor` | 1 |
| semi-sótano interior | `floor` | 1 |

The acquisition side corrected one room, the ground floor and the heat pump as extraction
version 3; their rows fall once its replay has landed in Silver.
