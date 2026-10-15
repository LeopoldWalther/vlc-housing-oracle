# Review — FEATURE-001: Silver on Iceberg against landing/v1

**Reviewer:** `reviewer` · **Date:** 2026-10-15 · **Plan:** [FEATURE-001](../plans/FEATURE-001-silver-rebuild.md)
**Verdict:** ✅ Approved

## Summary

The outcome is feasible as planned, and its central evidence reproduces. Re-running the spike
showed one data-integrity gap the plan did not anticipate: PyIceberg silently accepts an overwrite
that **drops** a column, and a naive Arrow schema comparison never matches. Both have an
unambiguous fix backed by that evidence. The review also found that the plan's total orders
silently rely on key uniqueness in landing, which nothing enforces. All corrections are local and
technical, recorded below and carried into the technical plan, so no product decision remains open.

## Evidence checked

- **Plan and dependencies:**
    - `dev/plans/FEATURE-001-silver-rebuild.md` (🔵) and its registry row.
    - ADR 0001, `CLAUDE.md`, `.github/workflows/ci.yml`, `requirements*.txt`, `pyproject.toml`,
        `.gitignore`.
    - `dev/tools/validate_workflow.py`, whose `ALLOWED_CHECKS` include `python-lint-and-test`,
        `workflow-consistency` and `terraform-validate`.
    - The private repository's landing contract: `schema.json` SHA-256 `f52989ab…2369b72`,
        recomputed on 2026-10-15 and unchanged.
- **Code, tests, data, and configuration:**
    - `src/` holds only `.gitkeep`, and `contracts/` does not exist, which matches the plan.
    - `python dev/tools/validate_workflow.py` passes.
    - The leak guard's `find_leaks` over the plan, the contract `schema.json` and its private
        README returns `[]`.
    - `.gitignore` already excludes `*.tfvars`, `*.tfstate` and `.terraform/`, and keeps
        `.terraform.lock.hcl` tracked.
    - Terraform 1.14.6 is installed locally.
- **Evidence reviewed** (scratchpad venv: DuckDB 1.5.6, PyIceberg 0.12.0, PyArrow 26.0.0):
    - **Spike reproduced.** Overwriting with 3 and then 5 rows gives snapshots `APPEND`,
        `DELETE`, `APPEND`. Time travel to the first snapshot returns 3 rows, the current snapshot
        5. DuckDB `iceberg_scan` reads the table back with UTC instants.
    - **Parquet `DESCRIBE` round trip:** a typed `COPY … TO parquet` of every contract type reads
        back as exactly `INTEGER`, `TIMESTAMP WITH TIME ZONE`, `DATE`, `VARCHAR[]`, `DOUBLE`,
        `BOOLEAN` and `VARCHAR`, including an all-null `INTEGER`. The plan's contract check by
        `DESCRIBE` is therefore sound.
    - **Schema mismatch behavior of `Table.overwrite`:**

        | Change | PyIceberg's response |
        | --- | --- |
        | extra column | `ValueError` |
        | `int` → `long` | `ValueError` |
        | `int` → `string` | `ValueError` |
        | **missing column** | **accepted**, written as nulls |

        Separately, `arrow.schema.equals(table.schema().as_arrow())` is `False` even for identical
        data: Iceberg returns `large_string`, `large_list<element>` and `PARQUET:field_id`
        metadata, while DuckDB's list child is named `l`. See M1.
    - **Redaction r1 in DuckDB:** all plan fixtures behave as stated.
        - `612 345 678`, `+34612345678.`, `0034 612-345-678`, `+34 712 345 678h`, `Tel:612345678`
            → `[phone]`.
        - `Sra. García` and `Dña. Úrsula` → `[name]`.
        - The two-numbers-plus-name sentence has all three replaced.
        - `690.000 €`, `Precio 1.690.000 €`, `Ref 123456789` and `reformado 2023` survive.
    - **Catalog from the environment:** `load_catalog('test')` with only
        `PYICEBERG_CATALOG__TEST__URI` and `__WAREHOUSE` set, plus `PYICEBERG_HOME` pointing at a
        temporary directory, returns a `SqlCatalog`. The CLI test is therefore feasible without
        touching a developer's `~/.pyiceberg.yaml`.
    - `pyiceberg[glue]` resolves `boto3`. The download was interrupted by the network, but the
        extra exists.
- **Unverified assumptions:**
    - **Glue from the operator's laptop (plan Assumption 4).** It needs real AWS, so it cannot
        be verified here. Owner: the operator. Verification point: task 4.2.
    - **Landing key uniqueness in prod data (M2).** It needs private data. Owner: the operator.
        Verification point: task 4.2, where the rebuild fails loudly if the keys are not unique.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | Four tables, explicit exclusions (`listing_event` → FEATURE-002, no expiry, no evolution) |
| Repository and dependency assumptions match reality | Pass | Empty `src/`, missing `contracts/`, allowed checks and contract digest all verified |
| Supporting evidence is reproducible and sufficient | Pass | Spike reproduced; schema-mismatch and redaction behavior measured (M1) |
| Design is the simplest adequate option | Pass | Functions, injected connection/catalog/clock, no sink interface until Gold exists |
| TDD slices and validation commands are executable | Pass | After the corrections: per-table key checks, uniqueness, schema comparison specified |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | After the M1–M3 corrections; synthetic-only fixtures; redaction measured |
| Operations, recovery, migration, and cost are addressed | Pass | ~$0.02/month, Glue free tier, rerun as recovery; IAM deferral accepted as a risk |

## Strengths

- **The contract check is by `DESCRIBE` equality against the pinned file.** The review's round
    trip confirms it is exact for every contract type, so a drifted landing fails before any
    commit.
- **Every Arrow table is computed before the first commit.** This narrows the cross-table
    non-atomicity to commit failures, and `rebuild_started_at` makes such a failure visible.
- **The plan reports the post-commit `APPEND` snapshot, not the `DELETE`.** That is the only
    choice that makes "pin a snapshot" mean something.
- **Glue was preferred over S3 Tables to keep snapshot lifetime under this repository's
    control,** consistent with ADR 0001.
- **Rules already reviewed in the private repository are reused** — dedupe orders, the empty-card
    rule, `r1` with `'g'` — instead of being reinvented.

## Findings

### 🟡 M1 — The sink's schema check is unspecified, and PyIceberg alone accepts a dropped column

- **Problem:** the plan says an existing table is overwritten "when its schema equals the new one,
    else `SilverSchemaError`", but does not say how to compare.
    - Arrow schema equality is always false between DuckDB output and an Iceberg table's schema.
    - PyIceberg's own check rejects extra columns and type changes, but writes a **missing**
        column as nulls.
- **Evidence:** the review spike above (four mismatch cases, and `equals` → `False`).
- **Impact:** an Implementer comparing Arrow schemas raises `SilverSchemaError` on every second
    rebuild. One relying on PyIceberg alone lets a SQL edit that loses a column overwrite Silver
    with an all-null column, silently.
- **Recommendation:** the sink first compares the ordered column names of the new Arrow table with
    `[f.name for f in table.schema().fields]` and raises `SilverSchemaError` naming the difference.
    It then calls `overwrite` and re-raises PyIceberg's `ValueError` as `SilverSchemaError` (with
    `from`) for type mismatches. Both happen before any commit, so the table stays unchanged.
    Task 3.1's RED covers a missing column, an extra column and a type change.
- **Disposition required:** **fixed in the technical plan** (task 3.1).

### 🟡 M2 — The total orders rely on landing key uniqueness that nothing enforces

- **Problem:** the `observation` order ends at `observed_at, ingested_at` and the `listing` order
    at `operation`. Both are total only if landing never repeats a row for the same area.
    `listing_text` joins `listing_detail` on `(area, source_listing_id, observed_at)`, which
    duplicates rows if that key repeats. The contract README promises the one-to-one join but
    documents no uniqueness.
- **Evidence:**
    - The plan's own success criterion asserts uniqueness in the output but nothing at the input.
    - Bronze is keyed by `(listing_id, observed_date)` per area partition (private FEATURE-013
        evidence), so uniqueness per area is expected but unasserted.
- **Impact:** if landing ever violates this, two rebuilds can pick different rows, which breaks the
    idempotency guarantee, or `listing_text` silently gains duplicates.
- **Recommendation:** `register_landing` raises `LandingContractError` (counts only) when either of
    these keys repeats:
    - `observation`: `(source, area, source_listing_id, observed_date)`;
    - `listing_detail` and `listing_text`: `(source, area, source_listing_id, observed_at)`.

    It also raises when a `listing_detail` key has no `listing_text` row or the reverse. That moves
    the plan's 2.5 "chosen record without text" error to the boundary, where every contract error
    already lives. Task 4.2 confirms the keys are unique on real landing.
- **Disposition required:** **fixed in the technical plan** (task 2.1; 2.5 simplified).

### 🟡 M3 — "Key columns" are listed globally, and `is_empty_card` can be null

- **Problem:** the plan's null check names `source, area, source_listing_id, observed_at,
    observed_date` for every table, but `walk` has neither `source_listing_id` nor `observed_at`.
    Separately, `observation`'s order sorts `is_empty_card` ascending, and DuckDB puts `NULL` last
    in ascending order, so a null flag would lose to an empty card.
- **Evidence:** `contracts/landing/v1/schema.json` (`walk` columns) and DuckDB's default
    `ORDER BY … ASC NULLS LAST`.
- **Impact:** without a per-table list the Implementer has to invent one, and a null
    `is_empty_card` would flip the dedupe silently.
- **Recommendation:** the required non-null columns are, per table:

    | Table | Required non-null columns |
    | --- | --- |
    | `walk` | `source`, `area`, `observed_date` |
    | `observation` | `source`, `area`, `source_listing_id`, `observed_at`, `observed_date`, `is_empty_card` |
    | `listing_detail` | `source`, `area`, `source_listing_id`, `observed_at`, `observed_date` |
    | `listing_text` | `source`, `area`, `source_listing_id`, `observed_at`, `observed_date` |

- **Disposition required:** **fixed in the technical plan** (task 2.1).

### 🟢 L1 — State that the seen columns count padding sightings

- **Evidence:** the plan derives `first_seen`, `last_seen` and `days_observed` from `observation`,
    which keeps empty cards. The private FEATURE-013 review settled that a listing's `last_seen`
    "counts every sighting", padding included.
- **Suggestion:** make it explicit. The seen columns are taken over every Silver `observation` row,
    empty cards included, and `days_observed` = the number of such rows (one per day after the
    dedupe). Added to task 2.4.
- **Benefit:** FEATURE-002 can rely on the definition without re-deriving it.

### 🟢 L2 — Spell out the location columns of an observation-only listing

- **Evidence:** the plan says "attributes are null for a listing without any detail record", and
    `area`, `operation`, `municipality` and `district` are `listing_detail` columns.
- **Suggestion:** say so in task 2.4's acceptance criteria: such a listing's area columns are null,
    and consumers take its area from `observation`.
- **Benefit:** removes the one place an Implementer might take an area from a padding card.

### 🟢 L3 — Small path and isolation precisions

- **Evidence:**
    - The plan's file list says `.terraform.lock.hcl` without a directory.
    - The CLI test would read a developer's `~/.pyiceberg.yaml` unless `PYICEBERG_HOME` is set.
    - The CI job needs a pinned Terraform.
- **Suggestion:**
    - The lock file is `infra/silver/.terraform.lock.hcl`.
    - The CLI test sets `PYICEBERG_HOME` to its temporary directory.
    - CI uses `hashicorp/setup-terraform` with `terraform_version: 1.14.6`.
- **Benefit:** deterministic tests and a reproducible infra check.

### 🟢 L4 — Prefer visibly fake phone numbers in fixtures

- **Evidence:** the fixtures use realistic numbers such as `612 345 678`, which could be assigned to
    a real person, in a public repository.
- **Suggestion:** keep the formats but use repeated digits (`600 000 000`, `+34 700 000 000`) where
    a test does not depend on the digits. Not required: no fixture pairs a number with a name or a
    listing.
- **Benefit:** removes any doubt under the "no personal data in fixtures" rule.

## Alternatives considered

- **Iceberg schema evolution (`union_by_name`) instead of failing on mismatch** — it would let a SQL
    change add columns without a manual step. Trade-off: a silent schema change inside
    `silver-v1` contradicts the rule that a change of meaning is a new `transformation_version`.
    Verdict: stick with the plan; evolve deliberately in a later feature.
- **A hash tiebreaker instead of enforcing uniqueness (M2)** — it makes the orders total
    regardless of landing. Trade-off: a duplicate landing row would then be absorbed silently.
    Verdict: fail loudly, per `CLAUDE.md`.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| `GlueCatalog` or the S3 FileIO fails from the laptop | Med | Med | Last task; local `SqlCatalog` is the tested fallback | Operator; 4.2 fails before any commit |
| Real landing violates per-area key uniqueness | Low | Med | Boundary check fails with counts (M2) | Operator; 4.2 exits 1 naming the table |
| Commit failure leaves tables at different rebuilds | Low | Low | Arrow computed first; `rebuild_started_at` per snapshot; rerun | Operator; mixed `rebuild_started_at` |
| `location` / `features` carry personal data | Low | Med | 4.2 counts-only probe of both; a hit means `r2` | Operator; probe count > 0 |
| Operator credentials broader than needed | Med | Low | No new principal created; least-privilege policy deferred | Accepted residual risk |

## Effort check

- **Plan estimate:** L (~16-20h)
- **Reviewer estimate:** L (~19h) — confidence Med
- **Why it differs / hidden complexity:** the plan counts 14 tasks. The M1–M3 boundary checks add
    about an hour to 2.1 and 3.1. Glue adds round-trip risk to 4.2 but no code.

## Reuse & conflicts

- **Reuse:**
    - `dev/tools/check_leaks.py` stays the leak gate.
    - The private repository's landing contract is copied byte for byte, nothing else.
    - `src/landing/rebuild.py` there shows the `SET TimeZone = 'UTC'` precedent.
- **Conflict / coordinate with:**
    - `requirements.txt` is touched by 1.2 and 3.1, so they are serialized.
    - `.github/workflows/ci.yml` is touched only by 4.1.
    - No other open feature.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-001-technical-plan.yaml`
- **Tasks:** 14; dependency graph acyclic and critical path verified: yes.
- **TDD contract:** RED tests, focused commands and expected failures named for all 12 behavior
    tasks: yes. 4.1 and 4.2 are validation-first.
- **File boundaries:** create/modify paths contained in allowed files; no overlap with forbidden
    paths: yes.
- **Git workflow:** base `main`, integration `feature/silver-rebuild/integration`, dependency-ordered
    merges, push only with approval: yes.
- **Required checks:** `python-lint-and-test`, `workflow-consistency`, `terraform-validate`.

## Approval criteria

- **Blockers (must fix):** none.
- **Required dispositions:**
    - M1 (schema check) — fixed in the technical plan, task 3.1.
    - M2 (key uniqueness) — fixed in the technical plan, task 2.1.
    - M3 (per-table key columns, `is_empty_card`) — fixed in the technical plan, task 2.1.
- **Optional:** L1–L4, adopted in the technical plan.
- **Accepted residual risk:**
    - Operator credentials stay broader than needed until a least-privilege policy feature.
    - Glue is verified only in 4.2.
    - `location` and `features` stay verbatim under `r1`, pending the 4.2 probe.
- **Technical plan:** emitted.

## Next step

Run `/implementer FEATURE-001`.

Record post-implementation learning separately under `dev/plans/implementations/`; do not rewrite
the review decision after implementation.
