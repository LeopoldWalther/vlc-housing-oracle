# FEATURE-006 — Redaction r2

**Status:** 🔵 Planned · **Effort:** S (~3h) · **Priority:** High
**Branch root:** `feature/redaction-r2` · **Created:** 2026-11-01 · **Updated:** 2026-11-03

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-006.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-006-technical-plan.yaml`.

## Objective

Close the two holes in redaction r1 that the FEATURE-005 residue scan found in real Silver
descriptions, a phone number glued to its country code and a name written in capitals, by a new
redaction version `r2`, and apply it to all of Silver's history with one backfill.

## Context

- **Current state:** Descriptions are redacted by the DuckDB macro `redact_personal` in
    [`src/silver/sql/redact.sql`](../../src/silver/sql/redact.sql) before they enter
    `listing_text`; every row and every commit records `redaction_version = 'r1'`
    ([`src/silver/rebuild.py`](../../src/silver/rebuild.py), `REDACTION_VERSION`). Titles, location
    and features stay verbatim by design. [`tests/test_silver_redaction.py`](../../tests/test_silver_redaction.py)
    specifies r1 on synthetic strings. [`docs/silver-rebuild.md`](../../docs/silver-rebuild.md)
    states that a pattern change is `r2`, never an edit of `r1`, and that `--mode backfill` is the
    way a rule change reaches history.
- **Problem:** [`analysis/silver-quality/06_redaction.sql`](../../analysis/silver-quality/06_redaction.sql)
    (FEATURE-005 task 2.6, snapshot set of 2026-10-31, 19,038 descriptions) found:
    - `phone_glued_country_code` = 1: `34` directly followed by a mobile or landline number, no
        separator. r1 accepts a bare prefix only as `+34` or `0034`, and its `\b` alternative cannot
        start inside `34…`, so the eleven digits survive. A contact word precedes the match, so it
        is very likely a real phone number.
    - `name_not_capitalised` = 3: an honorific followed by a word r1's `[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+`
        does not take. One is an honorific plus a word in capitals (very likely a surname); two are
        an honorific plus a two-letter lower-case word (not names).
    - Every other residue class is zero or not personal (`phone_inside_longer_number` = 11, judged
        reference numbers; 23 web addresses of agencies; 0 e-mails). Titles, location and
        features show no residue at all.
- **Constraints:** privacy outranks simplicity; the repository is public, so fixtures are
    synthetic and the backfill's check prints counts only. DuckDB uses RE2: no look-around, so a
    rule cannot assert "no digit before or after".

## Scope

- **In scope:** the r2 patterns, `REDACTION_VERSION = "r2"`, tests, the docs line that describes
    the redaction, one AWS backfill of Silver and its equality check, and a rerun of
    `06_redaction.sql` on the new snapshots.
- **Out of scope:** redacting titles, location or features (no residue found); web addresses
    (agencies, not people); numbers inside longer numbers (references); expiring the Iceberg
    snapshots that still hold r1 text (see risks); a new `transformation_version` (the redaction
    has its own version for exactly this case).
- **Users / consumers:** every reader of `listing_text`; the FEATURE-005 findings document.

## Dependencies

- **Needs:** FEATURE-004 (incremental Silver in AWS, backfill path) — complete. FEATURE-005 task
    2.6 provides the residue scan used as the acceptance check.
- **Unblocks:** any Gold feature or model that uses description text.

## Open assumptions

- **Assumption:** the glued case is `34` + `[6789]` + eight digits with nothing in between. —
    **Evidence:** `phone_glued_country_code` matched `(?<![0-9])34[6789][0-9]{8}(?![0-9])` once;
    no separator variant (`34 6…`) is reachable by r1's `\b` alternative because a space before
    `6` already makes a word boundary, so r1 redacts the number and leaves only `34 `. —
    **If wrong:** the rerun of `06_redaction.sql` after the backfill shows a non-zero count.
- **Assumption:** an honorific followed by a word of two or more capitals is a name. —
    **Evidence:** the one case found; the two false positives are lower case and stay
    unredacted. — **If wrong:** over-redaction of an abbreviation after `Don`/`Sr`, which costs a
    word of text, not privacy.
- **Assumption:** r2 changes nothing beyond the two cases. — **Evidence:** on the pinned
    2026-10-31 `listing_text` snapshot (already r1-redacted), `34[6789]([ .-]?[0-9]){8}` matches
    1 of 19,038 descriptions and an honorific plus a capitals word matches 1; the r2 macro was
    checked in DuckDB on every synthetic string of Phase 1. — **If wrong:** the residue rerun and
    the redacted counts of task 2.2 show it.
- **Assumption:** a backfill rewrites `listing_text` from landing, which keeps the unredacted
    text privately. — **Evidence:** `listing_detail.sql`: "Text stays in landing; it enters Silver
    only redacted"; `docs/silver-rebuild.md`, backfill mode. — **If wrong:** the backfill cannot
    reach history; stop and replan.

## Design

- **Primary approach:** edit the macro, not its callers. The phone alternative gains a glued
    prefix without a word boundary, so it also matches after a letter (`Tel34…`) or inside a longer
    digit run, ordered before the bare boundary so it wins:
    `(\+34[ .-]?|0034[ .-]?|34|\b)[6789]([ .-]?[0-9]){8}`.
    The name word accepts either the capitalised or the all-capitals form:
    `\b(Sr|Sra|Srta|Don|Doña|Dña)\.?\s+[A-ZÁÉÍÓÚÑ]([a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]+)`.
    The header comment says r2 and what it adds over r1. `REDACTION_VERSION` becomes `"r2"`.
- **Boundaries and data flow:** unchanged; only the macro body and one constant.
- **Contracts and invariants:** `listing_text.description` holds r2-redacted text, every row
    records `redaction_version = 'r2'` after the backfill. Rows written by incremental runs between
    the image deploy and the backfill carry r2 too, so the version column stays truthful per row.
    No schema change.
- **OOP / SOLID:** none needed; a SQL macro and a constant.
- **Patterns:** None.
- **Deliberately rejected complexity:** a Python redaction layer with look-around (RE2 suffices);
    versioned macros side by side (history is replaced by the backfill, nothing reads r1 any more).

## Approach

### Phase 1 — r2 patterns, test-first

- [ ] **1.1 Glued country code.** RED: add `"34600000000"` → `"[phone]"` and
    `"Tel34600000000"` → `"Tel[phone]"` to `REDACTED`, plus a guard that `"Ref 3412345678"` stays
    unchanged (a `34` followed by `1`), and change the version test to expect `"r2"`; run
    `tests/test_silver_redaction.py` and see the two glued cases and the version fail. GREEN: the
    `34` alternative and `REDACTION_VERSION = "r2"`; update the `"r1"` expectations in
    `tests/test_silver_rebuild.py` and `tests/test_silver_listing_incremental.py`. REFACTOR: macro
    comment.
- [ ] **1.2 Names in capitals.** RED: `"Sr. PÉREZ"` → `"[name]"` and `"Dña GARCÍA llama"` →
    `"[name] llama"`; add `"Sra. de"` and `"Sr. y"` to `UNCHANGED` (lower-case words survive).
    GREEN: the alternation in the name word. REFACTOR: none expected.
- [ ] **1.3 Docs.** `docs/silver-rebuild.md`: the `listing_text` row and the snapshot example
    name r2 and its two additions.

### Phase 2 — history

Phase 2 starts only after the Phase 1 PR is merged to `main` (user approval, GitHub activity
hours): GitHub Actions builds the image only on a push to `main` and tags it with the commit SHA
(review M1).

- [ ] **2.1 Deploy and backfill (operator, no commit).** Wait for the image of the merge commit,
    set `silver_image` to its SHA tag in the operator's local, gitignored `silver.auto.tfvars`, and
    `terraform apply`; then, in the same session, run the manual backfill from
    `docs/silver-rebuild.md` with the event source mapping disabled (review L1). Validation: the
    task exits 0, the new `listing_text` snapshot summary says `redaction_version = r2`, and the
    equality check against a local backfill of the same landing matches row counts per table.
- [ ] **2.2 Residue rerun and record.** Pin `06_redaction.sql` to the new snapshot ids in a
    private copy and run it: `phone_glued_country_code` = 0, `name_not_capitalised` ≤ 2 (the two
    lower-case false positives), `r1_phone_residue` = 0, and no row of the current `listing_text`
    has a `redaction_version` other than `r2`. Record the counts, never values, in
    `dev/plans/implementations/FEATURE-006-redaction-r2.md` and close the plan (review M2);
    FEATURE-005's findings cite that note.

## Files

- **Change:** `src/silver/sql/redact.sql` — r2 patterns and comment.
- **Change:** `src/silver/rebuild.py` — `REDACTION_VERSION = "r2"`.
- **Change:** `docs/silver-rebuild.md` — redaction description.
- **Create:** `dev/plans/implementations/FEATURE-006-redaction-r2.md` — backfill and residue counts (task 2.2).
- **Tests:** `tests/test_silver_redaction.py` — new cases; `tests/test_silver_rebuild.py`,
    `tests/test_silver_listing_incremental.py` — version expectation.

## Test strategy

- **Unit:** the macro through `build_tables` on synthetic descriptions: glued prefix, prefix with
    leading letters, capitals after every honorific form tested, survivors (`34` + non-mobile
    digit, lower-case words, prices, references, years).
- **Contract / fixtures:** synthetic only; numbers use repeated digits.
- **Integration:** existing rebuild and incremental suites with the new version string.
- **Retries / idempotency / evolution:** a rerun of the backfill produces the same rows; no schema
    evolution.
- **Configured quality checks:** `.venv/bin/python -m unittest`, `.venv/bin/ruff check .`,
    `.venv/bin/ruff format --check .`, `python dev/tools/check_leaks.py`,
    `python dev/tools/validate_workflow.py`.
- **Manual:** task 2.2's counts.

## Operational and compliance impact

- **Data sourcing / legal:** N/A, nothing crosses the landing contract.
- **Privacy / security:** removes one phone number and one name from current Silver. Older
    Iceberg snapshots keep the r1 text until snapshots are expired; they live in the private lake
    bucket only, as does landing's unredacted text.
- **Observability:** the snapshot summary's `redaction_version`.
- **Failure and recovery:** a failed backfill leaves the previous snapshots; rerun it.
- **Migration / compatibility:** no schema change; the FEATURE-005 queries pinned to the
    2026-10-31 snapshots keep working as long as those snapshots exist.

## Estimated monthly cloud cost

$0/month — no resources added or changed. The one-off backfill is a few Fargate minutes and an
Athena scan of a few MB: well under $0.10.

## Success criteria

- [ ] The new redaction tests failed first and pass; the full suite, ruff and the leak guard pass.
- [ ] Silver's current `listing_text` records `redaction_version = 'r2'` on every row (0 rows otherwise).
- [ ] The residue rerun shows `phone_glued_country_code` = 0 and `name_not_capitalised` ≤ 2.
- [ ] The equality check matches row counts between the AWS and a local backfill.
- [ ] No real value appears in the repository, in a log, or in the chat.

## Open questions & risks

- **Question:** expire the Iceberg snapshots older than the backfill, so the r1 text disappears
    from the lake too? Doing so breaks the FEATURE-005 pinning. Owner: user; decide after
    FEATURE-005 closes.
- **Risk:** the boundary-free `34` over-redacts a reference number that starts with `34` and a mobile digit. —
    *Mitigation:* accepted; privacy outranks a lost reference, and `phone_inside_longer_number`
    shows such numbers are rare.
- **Risk:** an incremental run starts during the backfill. — *Mitigation:* the documented
    procedure disables the event source mapping first.

## Progress log

- **2026-11-01** — Plan created from the FEATURE-005 task 2.6 residue scan.
- **2026-11-03** — Reviewed and approved (`dev/reviews/REVIEW-FEATURE-006.md`). Folded in: M1, the
    image comes from a merge to `main` and the tfvars file is a local operator file; M2, the record
    goes to this feature's implementation note and the "≥ r1 counts" criterion is replaced; L1, apply
    and backfill in one session.
