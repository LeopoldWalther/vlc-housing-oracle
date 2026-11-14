# Review — FEATURE-005: Silver data-quality exploration

**Reviewer:** `reviewer` · **Date:** 2026-10-31 · **Plan:** [FEATURE-005](../plans/FEATURE-005-silver-quality-exploration.md)
**Verdict:** ✅ Approved (with corrections folded into the plan)

## Summary

The outcome is feasible and well bounded: read-only, aggregate-only Athena queries plus a findings
document, with no production code, matching the user's choice of exploration only. Reproducing the
architect's evidence showed six measurement defects. Left as they were, the duplicate and
redaction numbers would have been wrong or unrepeatable. Each one has an unambiguous correction
from repository or query evidence, so the reviewer folded the corrections into the plan rather
than sending it back. One finding (M6) also exposes a probable privacy defect in Silver itself.
It needs a user decision outside this feature.

## Evidence checked

- **Plan and dependencies:**
    - [FEATURE-005](../plans/FEATURE-005-silver-quality-exploration.md) as written on 2026-10-30;
    - FEATURE-004 (🟢 Complete) in [`dev/plans/README.md`](../plans/README.md);
    - [`docs/silver-rebuild.md`](../../docs/silver-rebuild.md) for the table rules.
- **Code, tests, data, and configuration:**
    - `contracts/landing/v3/schema.json` and every Silver table's Glue columns: no advertiser or
        agency column exists, confirming the plan's constraint;
    - `src/silver/sql/redact.sql`: `r1`'s phone pattern is
        `(\+34[ .-]?|0034[ .-]?|\b)[6789]([ .-]?[0-9]){8}`;
    - `tests/test_silver_redaction.py`: its planted cases all use a separator or `+`;
    - `dev/tools/validate_workflow.py`: the allowed checks;
    - `.gitignore`: nothing ignores an `analysis/` folder, so its files are tracked and covered
        by the leak guard.
- **Evidence reviewed** (Athena, workgroup `primary`, aggregates only, 2026-10-31):
    - **Reproduced:** the tier counts, the completeness counts (detail records 19,038, floor
        13,041), and the first-sweep pattern.
    - **New: area first walks.** All 106 areas were first walked between 2026-09-23 and 09-30.
        Of 4,817 listings first seen from 10-01, only 33 were first seen within a day of their
        area's first walk. The ramp-up therefore ends on 09-30, the first collection day plus 7,
        and "after" must be strict.
    - **New: phone residue.** Of the architect's 8 "phone-like" matches, 12 match-sites sit
        inside longer digit runs, 0 are glued to a letter and 0 stand alone. These are false
        positives of a pattern without `r1`'s boundary. A separate check for a glued country
        code, `(?<![0-9])34[6789][0-9]{8}(?![0-9])`, matches 1 description: a form `r1` cannot
        catch.
    - **New: time travel.** `SELECT count(*) FROM listing FOR VERSION AS OF <id>` succeeds, so
        Athena can pin snapshots.

    These numbers do not prove precision. Whether matched pairs are the same flat is exactly
    what task 3.1 measures.
- **Unverified assumptions:**
    - Whether the glued `34…` match is a real phone number. Owner: the user, checked privately
        (M6).
    - The precision of each duplicate stratum. Owner: the operator, measured in task 3.1.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | Six named queries, a manual check with a fixed sample, a findings doc; exploration only per the user's decision |
| Repository and dependency assumptions match reality | Pass | No advertiser field (verified); Glue tables and Athena v3 present; time travel works |
| Supporting evidence is reproducible and sufficient | Pass after correction | M1–M6 and L1 correct the measurement design; the corrected plan reproduces |
| Design is the simplest adequate option | Pass | SQL files plus a document; rejecting a notebook and a Python module is justified |
| TDD slices and validation commands are executable | N/A | No production behavior; each task has a validation-first check against a known answer |
| Data integrity, data sourcing, privacy, and security are addressed | Pass after correction | Aggregate-only rule, private drill-downs, M6 escalation |
| Operations, recovery, migration, and cost are addressed | Pass | Read-only; ~$0; L2 covers retention of private result files |

## Strengths

- Flag-never-filter is fixed before any rule exists, which keeps facts and inferred labels
    apart as `CLAUDE.md` requires.
- The plan records the absence of an advertiser field as a constraint rather than assuming one.
- The ramp-up and censoring windows, which the user supplied and the data confirm, guard every
    temporal metric against the multi-day sweep.

## Findings

### 🟡 M1 — Tier A's equal-price key makes "price spread inside clusters" meaningless

- **Problem:** Tier A required an equal asking price, yet task 2.4 measured the price spread
    inside its clusters. The spread is zero by construction. Pairs where agencies quote different
    prices for the same flat, a common case, were excluded from every tier.
- **Evidence:** the plan's design (tier A key) and task 2.4 (price spread) as written on
    2026-10-30.
- **Impact:** the duplicate rate is understated, and the follow-up feature would adopt an
    equal-price rule without ever having measured the alternative.
- **Recommendation:** add the shape key A0 (no price) and the tolerance tiers A±5 and A±10. Base
    B on A±5, and measure the price-gap distribution within A0 pairs.
- **Disposition:** fixed in the plan (Design and task 2.4).

### 🟡 M2 — Nested tiers sampled as wholes do not show where precision drops

- **Problem:** C ⊂ B ⊂ A, so "30 pairs per tier" samples C's pairs again inside B and A. The
    precision of B alone (near but on a different or unknown floor) is never measured.
- **Evidence:** the nesting stated in the Design; task 3.1 as written.
- **Impact:** a threshold could be adopted for B whose added pairs are mostly false.
- **Recommendation:** sample exclusive strata (C; B without C; A±5 without B; A0 without A±10),
    concurrent pairs only, and add a "page gone" outcome.
- **Disposition:** fixed in the plan (task 3.1).

### 🟡 M3 — "Clusters" are undefined for tolerance and distance tiers

- **Problem:** the surplus (listings minus clusters) needs clusters. Pairs defined by distance
    or price tolerance are not transitive, and turning them into clusters needs connected
    components, which plain Athena SQL does not do well.
- **Evidence:** the definitions of tiers B and C; the surplus metric in task 2.4.
- **Impact:** the implementer would have to invent a clustering method mid-task.
- **Recommendation:** clusters only for exact-key groups (A). For the other tiers, report pairs,
    listings in at least one pair, and surplus bounds. Leave components to the follow-up feature.
- **Disposition:** fixed in the plan (Design and task 2.4).

### 🟡 M4 — Queries were not pinned to a snapshot

- **Problem:** the plan recorded snapshot ids, but its queries read the current tables. A rerun
    returns different numbers, and a query that overlaps a Silver task could join tables from two
    different runs.
- **Evidence:** Silver commits its tables one after another per run (`src/silver/rebuild.py`).
    Athena's `FOR VERSION AS OF` works on these tables (verified).
- **Impact:** findings that cannot be reproduced, and possibly inconsistent cross-table counts.
- **Recommendation:** pin every table reference with `FOR VERSION AS OF`. Keep the snapshot ids
    in one constant block per file, taken at a moment no Silver task runs.
- **Disposition:** fixed in the plan (Design).

### 🟡 M5 — Floor equality matched a missing floor to a missing floor

- **Problem:** the architect's evidence used `IS NOT DISTINCT FROM`, so two listings without a
    floor counted as being on the same floor.
- **Evidence:** 31% of detail records have no floor (13,041 of 19,038 have one).
- **Impact:** tier C is inflated by exactly the listings for which it has no evidence.
- **Recommendation:** C requires an equal, non-null floor.
- **Disposition:** fixed in the plan (Design). The architect's tier C figure (about 2,650) is an
    upper bound.

### 🟡 M6 — The redaction evidence was overstated, and a probable real leak was missed

- **Problem:** the plan's 8 "phone-like" descriptions came from a pattern without `r1`'s
    boundary; all are digit runs inside longer numbers. Meanwhile one description holds a mobile
    number with a glued `34` country code. `r1` cannot catch that form, because `\b` fails between
    two digits.
- **Evidence:** the classification queries in Evidence checked; `src/silver/sql/redact.sql`.
- **Impact:** inside the plan, task 2.6 would measure the wrong thing. Outside it, Silver may
    hold one contact detail in a table that is meant to be redacted (privacy, project priority 2).
- **Recommendation:**
    - Task 2.6 uses `r1`'s exact pattern and classifies residue by its surroundings, validated
        against the test strings and a glued `34…` case.
    - The user privately checks the one match. If it is a phone number, a separate bugfix adds
        the glued form under `redaction_version` `r2` with a failing test first. `r2` is applied
        by a deliberate backfill of `listing_text`, never by an edit of `r1`.
- **Disposition:** the plan is fixed (assumption and task 2.6). The `r2` decision is the user's
    and lies outside FEATURE-005.

### 🟢 L1 — Tie the ramp-up to measured sweep completion

- **Evidence:** all 106 areas were first walked by 2026-09-30, the first day plus 7. The 30 and
    1 listings first seen on 09-23 and 09-24 come from single-area test days.
- **Suggestion:** define `RAMP_UP_END` as the day every area had been walked, with a strict
    "after", plus a per-area ramp-up for an area added later.
- **Benefit:** the window stays right when the acquisition side adds areas.
- **Disposition:** taken into the plan.

### 🟢 L2 — Private drill-down results persist in the Athena result prefix

- **Evidence:** the workgroup's result location has no lifecycle rule. The drill-downs for task
    3.1 return listing ids, and those result files stay in the private bucket indefinitely.
- **Suggestion:** a lifecycle rule expiring the result prefix after 30 days, as a separate small
    infrastructure change or by hand.
- **Benefit:** private ids do not accumulate. Safe to skip, because the bucket is private.

## Alternatives considered

- **DuckDB with PyIceberg on a laptop** — reads the Glue catalog without Athena. Trade-off: no
    per-query cost (already negligible) against credential and extension setup on the laptop.
    Verdict: stick with Athena, which is already set up and pin-capable.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| New-development units with identical attributes pass tiers B/C | Med | Med | Exclusive strata in the manual check; same-point versus near split | Operator; low precision in stratum C |
| A row value is committed in a query or the findings doc | Low | High | Aggregate-only final SELECTs; review of every file; leak guard | Implementer; review diff |
| A collection pause makes listings look missed or vanished | Med | Med | Judge "missed" only against sweeps that walked the area | Implementer; gaps cluster around pause days |
| Young data (about three sweeps after the ramp-up) | High | Low | Findings name their snapshot; queries are rerunnable | User; re-measure before a liquidity use case |

## Effort check

- **Plan estimate:** S (~8h)
- **Reviewer estimate:** M (~10h) — confidence Med
- **Why it differs / hidden complexity:** the manual check over four strata of 30 pairs is about
    two to three hours of the operator's time. The tolerance and distance pair joins need care to
    stay aggregate-only and to nest.

## Reuse & conflicts

- **Reuse:** `src/silver/sql/redact.sql` — the exact `r1` patterns for task 2.6;
    `tests/test_silver_redaction.py` — the validation strings.
- **Conflict / coordinate with:** a possible `r2` bugfix (M6) touches
    `src/silver/sql/redact.sql` and `src/silver/rebuild.py`, which are files this feature does
    not touch. If it lands first, task 2.6 runs against `r2` and says so.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-005-technical-plan.yaml`
- **Tasks:** 9; dependency graph acyclic and critical path verified: yes.
- **TDD contract:** N/A. No behavior task; every task has a validation-first check and
    `tdd: null`.
- **File boundaries:** create/modify paths are contained in allowed files, with no overlaps:
    yes.
- **Git workflow:** base `main`, integration branch, dependency order: yes.
- **Required checks:** `python-lint-and-test`, `workflow-consistency`.

## Approval criteria

- **Blockers (must fix):** none.
- **Required dispositions:**
    - M1–M5: fixed in the plan;
    - M6: fixed in the plan; the `r2` decision goes to the user.
- **Optional:**
    - L1: taken;
    - L2: left to the user.
- **Accepted residual risk:** the precision of each tier is unknown until task 3.1; the plan
    adopts no rule before it.
- **Technical plan:** emitted.

## Next step

Run `/implementer FEATURE-005`. Separately, the user decides on M6: check the one glued `34…`
match privately and, if it is a phone number, request an `r2` bugfix.
