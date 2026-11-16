# Review — FEATURE-010: Labels from listing features, and a check for unread features

**Reviewer:** `reviewer` · **Date:** 2026-11-16 · **Plan:** [FEATURE-010](../plans/FEATURE-010-feature-labels.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible and its numbers reproduce exactly: 291,472 feature entries reduce to 100
wordings, and the three meanings sit in five of them. The feature is additive and follows the
path of `listing_quality`. Four corrections were needed:

- one of the 100 wordings holds a word the leak guard forbids, so it cannot be committed in
    clear;
- nine wordings state a meaning that no column carries, and the registry had no target for
    them;
- the normalisation and "target missing" were not defined exactly enough to reproduce the
    counts;
- the alarm's behaviour between runs was open.

They are folded into the plan. The user answered the open question: the wordings may be public.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-010-feature-labels.md` (🔵 Planned,
    registered). FEATURE-004 and FEATURE-008 are 🟢 Complete. FEATURE-013 and FEATURE-009 are
    implemented and in review as pull requests; this review read the code of the FEATURE-009
    branch.
- **Code, tests, data, and configuration:**
    - `src/silver/sql/listing_text.sql`: `features` is the detail record's list, verbatim.
    - `src/silver/rebuild.py`: `_labels_from_silver` reads `listing` once with the columns of
        the label rules, and `listing_quality()` then replaces the temporary table
        `silver_listing` with its own columns; `_prepare` runs before every statement;
        `_publish_pending` publishes one metric, and only `main` knows the run's result.
    - `src/synthetic/landing.py`: every detail record has the features `80 m²` and
        `3 habitaciones`. The first reduces to a wording that is not in the real vocabulary.
        No listing is a rental, and `_detail` takes no features.
    - `infra/silver/alerts.tf`: the alarm on `PendingRuns` treats missing data as not
        breaching.
    - `contracts/landing/v3/schema.json`: the columns of `listing_detail`, which are the
        attribute columns of `listing`.
    - `dev/tools/check_leaks.py`: a term matches as a whole lower-cased word.
- **Evidence reviewed:** Athena on Silver of 2026-11-16, vocabulary and counts only.
    - With the rule "trim, replace every number, lower case" the feature entries give exactly
        100 wordings, 86 of them on ten or more listings, over 291,472 entries. The review read
        all 100: each is a phrase of the portal's interface, the rare ones included (a basement
        floor, a kind of country house).
    - 1,157 listings carry the tenant wording and 71 the bare-ownership wording; the three
        furnishing wordings are on 4,488, 834 and 391 listings; 221 the illegal-occupation
        wording. All as the plan says.
    - Every one of the 23,159 text rows has a non-empty feature list.
    - The leak guard's `find_leaks` over the 100 wordings accepts 99 and refuses one, on 1,364
        listings.
    - Listings with one of three energy-certificate wordings (exempt, in process, not stated;
        2,035 listings) have no energy rating: no column carries the certificate's status.
    - `property_type` carries the house kinds: its counts equal the wordings' (2,621 detached,
        772 terraced, 387 semi-detached).
    - DuckDB: the normalisation written as one expression maps each of the 100 wordings to
        itself, and `sha256` equals Python's `hashlib` on all of them.
- **Unverified assumptions:**
    - Reading all of `listing` in every run adds no noticeable time. Owner: operator.
        Verification point: task 4.3 records the duration.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | After M2 and M3 |
| Repository and dependency assumptions match reality | Pass | After M1 |
| Supporting evidence is reproducible and sufficient | Pass | Every count of the plan reproduced |
| Design is the simplest adequate option | Pass | Two statements and one table in code |
| TDD slices and validation commands are executable | Pass | After M3; the registry is emitted with the technical plan |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | After M1; the leak guard is unchanged |
| Operations, recovery, migration, and cost are addressed | Pass | After M4; about $0.40 a month |

## Strengths

- The second table turns "found by hand, months later" into a count in every run.
- Labels are defined as "the portal's feature says so", which stays true whatever the text says.
- Meanings that belong to the acquisition side are reported and not duplicated.
- An unknown wording never fails a run.

## Findings

### 🟡 M1 — One wording cannot be committed in clear

- **Problem:** the plan committed all 100 wordings and had not run them through the leak guard.
- **Evidence:** the guard refuses one wording: it holds the source's word for a property, which
    is a forbidden term.
- **Impact:** the first commit of the registry would fail CI, and weakening the guard is not an
    option.
- **Recommendation:** the run joins wordings to the registry by SHA-256. Ninety-nine entries
    are written in clear and hashed in code; the refused one is listed by its digest alone,
    with its target. Tests, documentation and the synthetic data never spell it. The table
    `feature_pattern` is private and shows it in clear.
- **Disposition required:** fixed in the feature plan and in task 1.1.

### 🟡 M2 — No target for a meaning no column carries

- **Problem:** a target was a `listing` column, a label, or `none` for a wording that only
    repeats a category.
- **Evidence:** nine wordings state something no column holds: three on accessibility, two
    with the price of a parking space, one fireplace, three on the energy certificate's status.
- **Impact:** they would be filed under `none` and vanish from the report to the acquisition
    side, which is the table's second purpose.
- **Recommendation:** the target `unread`. With it every one of the 100 wordings has a
    column, a label or `unread`, so the planned target `none` is dropped. The implementation
    note lists the unread wordings with their counts for the acquisition side.
- **Disposition required:** fixed in the feature plan and in the registry of the technical plan.

### 🟡 M3 — Normalisation and "missing" were not exact

- **Problem:** "every number replaced by `#`" and "target column is null" leave room.
- **Evidence:** a number can have decimals; DuckDB's `regexp_replace` replaces only the first
    match without its global option; the `_state` columns say `unknown` and not null when the
    extractor found nothing; `orientation` is a list.
- **Impact:** other counts than Athena's, and a wording that the extractor misses would show
    zero missing targets.
- **Recommendation:** one SQL macro for the wording, used by both statements and pinned by a
    test on the registry; a target is missing when the column is null, `unknown` or an empty
    list.
- **Disposition required:** fixed in the feature plan and in tasks 1.1 and 1.2.

### 🟡 M4 — The alarm between runs

- **Problem:** "an alarm on a value above zero" did not say what happens when no run publishes.
- **Evidence:** a run with nothing pending writes nothing and knows no count; the existing
    alarm treats missing data as not breaching.
- **Impact:** with that setting the alarm would clear by itself after an hour and mail again
    with every run.
- **Recommendation:** the metric is published only by a run that processed something; the
    alarm ignores missing data, so it keeps its state until a run reports zero.
- **Disposition required:** fixed in the feature plan and in tasks 3.2 and 4.1.

### 🟢 L1 — The registry before the pattern table

- **Evidence:** the pattern table joins the registry.
- **Suggestion:** tasks 1.1 and 1.2 swap. Applied.
- **Benefit:** each task has a real RED.

### 🟢 L2 — The synthetic scenario needs no rental

- **Evidence:** the generator has only sale listings, and the furnishing label does not depend
    on the operation.
- **Suggestion:** a sale listing carries the furnishing wording. The generator's default
    features become two wordings of the registry, so that the synthetic landing has exactly one
    unknown wording. Applied.
- **Benefit:** no new area and no change to the walk scenarios.

### 🟢 L3 — One target per wording

- **Evidence:** a few wordings inform two columns, such as a floor with its outlook.
- **Suggestion:** the registry names the first. Applied and documented.
- **Benefit:** the table stays one row per wording.

## Alternatives considered

- **A private registry file passed by path** — nothing public. Trade-off: a second
    configuration to deploy, a registry nobody can review, and tests that cannot use the real
    vocabulary. Verdict: stick with the plan, with M1.
- **Every registry entry as a digest** — one form. Trade-off: an unreadable registry.
    Verdict: only the refused wording is a digest.
- **No metric and no alarm** — $0. Trade-off: an unknown wording is visible only in the task
    log. Verdict: stick with the plan; $0.40 a month buys the notice the feature exists for.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| A new wording is typed text with personal data | Low | Med | The run reports a count only; the table is private | Operator; the alarm |
| The label is read as certain | Med | Low | Documented as the portal's statement | Consumer; documentation |
| The registry goes stale | Med | Low | The alarm | Operator |
| A later term of the leak guard refuses a committed wording | Low | Low | The digest form exists for it | CI |

## Effort check

- **Plan estimate:** M (~8h)
- **Reviewer estimate:** M (~9h) — confidence High
- **Why it differs / hidden complexity:** the registry's digest form and the generator's
    default features.

## Reuse & conflicts

- **Reuse:** `listing_quality()` as the pattern for a rule over Silver's whole `listing`;
    `_labels_from_silver`; `_prepare`; `_publish_pending`; the alarm on `PendingRuns`; the
    "predates" tests of `tests/test_silver_incremental.py`.
- **Conflict / coordinate with:** FEATURE-009 changes `process_landing` and `_report`; this
    feature starts from `main` after its merge. FEATURE-011 and FEATURE-012 follow.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-010-technical-plan.yaml`
- **Tasks:** 9; dependency graph and critical path verified yes
- **TDD contract:** RED failures and commands verified yes
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `terraform-validate`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** M1 to M4 fixed in the feature plan and the technical plan
- **Optional:** L1 to L3 applied
- **Accepted residual risk:** the unmeasured task duration, measured in task 4.3
- **Technical plan:** emitted

## Next step

Run `/implementer FEATURE-010` after FEATURE-009 is merged to `main`.
