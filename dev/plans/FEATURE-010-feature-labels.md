# FEATURE-010 — Labels from listing features, and a check for unread features

**Status:** 🟢 Complete · **Effort:** M (~9h) · **Priority:** High
**Branch root:** `feature/feature-labels` · **Created:** 2026-11-16 · **Updated:** 2026-11-16

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-010.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-010-technical-plan.yaml`.

## Objective

Read three meanings that exist only in a listing's feature list into a Silver table: sold with a
tenant, sold as bare ownership, and how a rental is furnished. Add a second table that shows, for
every feature wording the portal uses, whether a column carries it, so that a wording nobody reads
is noticed in the next run and not months later.

## Context

- **Current state:**
    - `listing_text.features` holds each detail record's feature list verbatim
        ([`src/silver/sql/listing_text.sql`](../../src/silver/sql/listing_text.sql)).
    - The structured columns of `listing` come from the acquisition side's extractor. Its
        `missing_fields` names only price fields and `property_type`.
- **Problem** (aggregate counts on Silver of 2026-11-15):
    - 1,157 sale listings carry the feature "sold with tenants" and 71 "bare ownership". No
        column holds either. In the city, apartments sold with a tenant have a median of
        2,841 EUR/m² and bare ownership 2,135, against 3,368 for the rest: they look like
        bargains to any price model.
    - 5,713 of 5,923 rent listings state their furnishing in one of three feature wordings. No
        column holds it.
    - Several wordings feed a column that stays empty: one room (1,877 listings), ground floor
        (about 1,670 flats), heat pump (1,624). Each was found by hand.
    - The vocabulary is closed: with digits replaced, the 291,472 feature entries reduce to 100
        wordings, 86 of them on ten or more listings.
- **Constraints:**
    - Parsing the source belongs to the acquisition side. Silver adds a label only where no
        landing column is meant to carry the meaning; where one is, Silver reports the gap and
        does not duplicate the extractor.
    - Facts and inferred labels stay in separate tables.
    - The repository is public: it may hold generic vocabulary, never listing data.

## Scope

- **In scope:**
    - the Silver table `listing_feature_label`: `is_tenant_occupied`, `is_bare_ownership`,
        `furnishing`;
    - a registry of known feature wordings in the repository, each with the column or label it
        informs;
    - the Silver table `feature_pattern`: one row per wording seen, with its counts;
    - a report line, a metric and one alarm for wordings that are not in the registry.
- **Out of scope:**
    - labels for meanings a landing column should carry (rooms, floor, heating, accessibility,
        parking price): reported by `feature_pattern`, repaired upstream;
    - illegally occupied homes: `listing.is_occupied` already equals that feature (221 of 221);
    - auctions: they appear only in free text (FEATURE-012); seasonal rentals and deposits:
        the page states them, so they belong to the acquisition side;
    - any change to `listing`.
- **Users / consumers:** fair-price estimation and yield maps (exclude or model special sales,
    split rents by furnishing); the operator, who sees a new wording.

## Dependencies

- **Needs:** FEATURE-004 and FEATURE-008 — complete. Implemented after FEATURE-013 and
    FEATURE-009, which change the same function.
- **Unblocks:** a report to the acquisition side with exact counts per unread wording.

## Open assumptions

- **Assumption:** the three meanings are stated by fixed portal wordings, not typed by the
    advertiser. — **Evidence:** each occurs in exactly one wording (furnishing in three) over
    thousands of listings, and 93 of 100 wordings occur more than once. — **If wrong:** a
    variant shows up as an unknown wording.
- **Assumption:** the feature label agrees with the text. — **Evidence:** the description also
    mentions a tenant for 1,015 of the 1,157 listings and bare ownership for 67 of 71. —
    **If wrong:** the label is defined as "the portal's feature says so", which stays true.
- **Assumption:** the vocabulary may be committed. — **Evidence:** the user approved it on
    2026-11-16, and the review read all 100 wordings: each is a generic phrase of the portal's
    interface. The leak guard's own function accepts 99 of them and refuses one, which holds
    the source's word for a property. — **If wrong:** N/A, validated; the refused wording is
    listed by its SHA-256 digest, the way the leak guard lists its terms (review M1).

## Design

- **Primary approach:**
    - A wording is a feature string, trimmed, with every number (a run of digits, with groups
        after `.` or `,`) replaced by `#`, in lower case.
    - The registry is one table in code, `src/silver/feature_patterns.py`: wording and `target`,
        plus the furnishing value for the three furnishing wordings. A target is the `listing`
        column the wording informs (the first, when it informs two), a label of this feature,
        or `unread` for a meaning no column carries yet (review M2). No wording needs the
        planned target `none`, so it does not exist. The run joins wordings to the registry by their
        SHA-256, so one wording can be listed by digest alone.
    - `feature_pattern.sql` unnests the features, normalises them and joins the registry.
        `listing_feature_label.sql` reads the same normalised wordings.
    - Both tables are computed from Silver's `listing_text` and `listing` after a run has
        written them, and replaced whole, like `listing_quality`.
- **Contracts and invariants:**
    - `listing_feature_label`: one row per listing with a text row; `source`,
        `source_listing_id`, `is_tenant_occupied` and `is_bare_ownership` (boolean, never null),
        `furnishing` (`furnished`, `kitchen_only`, `unfurnished`, or null when no wording states
        it), `transformation_version`.
    - `feature_pattern`: one row per wording; `pattern`, `listings`, `target` (null for an
        unknown wording), `listings_target_missing` (listings with the wording whose target
        column is missing: null, `unknown`, or an empty list; null when the target is not a
        `listing` column), `transformation_version`.
    - The run reports `unknown feature patterns: <n>` and publishes it as the metric
        `UnknownFeaturePatterns` with the existing `--metrics` flag, only when it processed
        something. An alarm on a value above zero mails the existing topic; it ignores missing
        data, so it stays in alarm until a run reports zero and mails once per episode.
- **OOP / SOLID:** pure functions and SQL statements are sufficient.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - an alarm on `listings_target_missing`: it is above zero today for known upstream gaps and
        would never clear;
    - fuzzy matching of wordings: the vocabulary is closed and small;
    - one boolean per wording: a hundred sparse columns that repeat `listing`.

## Approach

### Phase 1 — Wordings

- [x] **1.1 The registry and the normalisation.** RED: two features that differ only in a
    number are one wording; every registry entry is already normalised and unique; every target
    is a `listing` column, a label of this feature or `unread`; the registry has 100 entries,
    one of them by digest.
- [x] **1.2 The pattern table.** RED: a wording in the registry carries its target; one outside
    it has a null target; `listings_target_missing` counts listings whose target column is
    missing. Fails because the function does not exist.

### Phase 2 — Labels

- [x] **2.1 Sale labels.** RED: a listing with the tenant wording is tenant-occupied; one with
    the bare-ownership wording is bare ownership; one with neither has false, never null; a
    listing with a detail record but an empty feature list has a row.
- [x] **2.2 Furnishing.** RED: each of the three wordings gives its value; none gives null; two
    conflicting wordings give null.

### Phase 3 — The run writes the tables

- [x] **3.1 Pipeline.** RED: a backfill of the synthetic landing writes both tables; stepwise
    commits give the same rows; a catalog that predates them gains them without a backfill.
- [x] **3.2 Synthetic scenario, report and metric.** RED: the generator holds a listing with
    a furnishing wording, a sale with a tenant and one feature outside the registry; the command
    prints both snapshots and `unknown feature patterns: 1`; with `--metrics` the fake client
    receives the value.

### Phase 4 — Operation

- [x] **4.1 Alarm.** One CloudWatch alarm in `infra/silver/alerts.tf`; `terraform validate` and
    `plan` show one resource added.
- [x] **4.2 Documentation.** `docs/silver-rebuild.md`: both tables, how to read them, and how to
    add a wording to the registry.
- [x] **4.3 Deployment and first run (operator, with approval).** Counts only, against the same
    rule in Athena: 100 wordings and 0 unknown; 1,157 tenant, 71 bare ownership; furnishing
    4,488 / 834 / 391. Recorded in `dev/plans/implementations/FEATURE-010-feature-labels.md`,
    with the counts per unread wording for the acquisition side.

## Files

- **Create:** `src/silver/feature_patterns.py`, `src/silver/sql/feature_wording.sql`,
    `src/silver/sql/feature_pattern.sql`, `src/silver/sql/listing_feature_label.sql`.
- **Change:** `src/silver/rebuild.py`, `src/synthetic/landing.py`, `infra/silver/alerts.tf`,
    `docs/silver-rebuild.md`, `README.md`.
- **Tests:** `tests/test_silver_feature_registry.py`, `tests/test_silver_feature_pattern.py`,
    `tests/test_silver_feature_label.py` (new),
    `tests/test_silver_incremental.py`, `tests/test_silver_cli.py`,
    `tests/test_synthetic_landing.py`.

## Test strategy

- **Unit:** normalisation, registry join, target-missing count, each label with and without its
    wording, conflicting wordings.
- **Contract / fixtures:** column names and types of both tables; registry consistency.
- **Integration:** a local SQLite catalog with a backfill and stepwise commits.
- **Retries / idempotency / evolution:** incremental equals backfill; a catalog that predates
    the tables.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`,
    `terraform -chdir=infra/silver validate`.
- **Manual:** the comparison of 4.3.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract. The registry holds generic
    interface wordings.
- **Privacy / security:** `feature_pattern` holds wordings and counts; a wording that occurs
    once could be typed text, so the table stays private like every Silver table.
- **Observability:** the report line, the metric and the alarm.
- **Failure and recovery:** written before the ingest log; an unknown wording never fails a run.
- **Migration / compatibility:** additive; no backfill.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| CloudWatch alarm | $0.10 per standard alarm | one alarm | ~$0.10 |
| CloudWatch custom metric | $0.30 per metric | one metric | ~$0.30 |
| **Total (new AWS components)** | | | **~$0.40/month** |

- **Cost drivers & cheaper alternatives:** the metric. Without metric and alarm the check costs
    $0 and is visible only in the task log.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [x] Every listing with a text row has one label row, and no boolean is null.
- [x] Every wording seen has one row in `feature_pattern`, and the real data has no unknown one.
- [x] A feature outside the registry is reported, published and alarmed.
- [x] The real run's counts equal the same rule in Athena.
- [x] Every behavior was implemented test-first and focused plus affected suites pass
- [x] Configured lint, format, leak, infrastructure and workflow checks pass
- [x] Both tables and the registry procedure are documented
- [x] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Answered (user, 2026-11-16):** the wordings may be committed to the public repository.
- **Question (user, before 4.3):** approve the alarm and the deployment.
- **Risk:** the label is read as "certain". — *Mitigation:* documented as "the portal's feature
    says so"; the text-based labels of FEATURE-012 are a second, independent reading.
- **Risk:** the registry goes stale. — *Mitigation:* that is what the alarm reports.

## Progress log

- **2026-11-16** — Plan created from the exploration of 2026-11-15; the second of four features.
- **2026-11-16** — The acquisition side corrected three of the unread wordings (one room, ground
    floor, heat pump) as extraction version 3. Their counts in `feature_pattern` fall to zero
    once its replay has landed; until then they show the gap.
- **2026-11-16** — Reviewed and approved. Corrections folded in: one wording is listed by
    digest because the leak guard refuses a word of it; the target `unread` for meanings no
    column carries; the exact normalisation and what "missing" means; the registry before the
    pattern table; the alarm's handling of missing data. The user approved the public registry.
- **2026-11-16** — Task 4.3: the image of merge commit `9be095f` was deployed, and the first
    run wrote both tables with no unknown wording; every count equals the same rule in Athena
    and the alarm is OK. See the [implementation note](implementations/FEATURE-010-feature-labels.md),
    which lists the unread wordings for the acquisition side. Feature complete.
