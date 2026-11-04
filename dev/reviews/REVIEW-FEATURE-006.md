# Review — FEATURE-006: Redaction r2

**Reviewer:** `reviewer` · **Date:** 2026-11-03 · **Plan:** [FEATURE-006](../plans/FEATURE-006-redaction-r2.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible and the smallest fix for the two holes it targets. The patterns behave as
claimed, both on synthetic strings and on the real descriptions, where they change exactly the
two residue cases and nothing else. Two corrections affect the operator phase: the image only
exists after a merge to `main`, and the backfill result belongs in an implementation note of
this feature, not in FEATURE-005's findings. Both follow unambiguously from the repository and
are folded into the plan. The next step is implementation.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-006-redaction-r2.md` (🔵 Planned, registered in
    `dev/plans/README.md`); FEATURE-004 🟢 Complete; FEATURE-005 task 2.6 `done` on
    `feature/silver-quality-exploration/integration`.
- **Code, tests, data, and configuration:**
    - `src/silver/sql/redact.sql` holds the r1 macro. `src/silver/rebuild.py:35` sets
        `REDACTION_VERSION = "r1"`; `:163` writes it into every snapshot summary and `:298` passes
        it to `listing_text.sql`.
    - `"r1"` as a redaction version is asserted in three places:
        `tests/test_silver_redaction.py:99`, `tests/test_silver_rebuild.py:79` and
        `tests/test_silver_listing_incremental.py:181`. The `'r1'` strings in
        `tests/test_silver_sink.py` are run ids and are unaffected.
    - Docs: `docs/silver-rebuild.md:105` and `:237`.
    - No other table holds free text: `listing_detail.sql:2` says "Text stays in landing".
    - `docs/silver-rebuild.md:137-139` says the image is built by GitHub Actions on a push to
        `main` and tagged with the commit SHA.
    - `.gitignore:79` ignores `*.auto.tfvars`, so `infra/silver/silver.auto.tfvars` is a local
        operator file.
- **Evidence reviewed:**
    - **DuckDB (RE2) spike:** reproduced the r2 macro on all 16 synthetic strings of Phase 1 and
        the existing r1 cases. Glued prefixes, prefixes after a letter and capitals after an
        honorific are redacted; `Ref 3412345678`, `Sra. de`, `Sr. y`, prices, references and years
        survive. This proves the patterns, not the test harness.
    - **Athena, pinned `listing_text` snapshot of 2026-10-31 (19,038 descriptions), counts only:**
        - The boundary-free `34` alternative matches 1 description, the capitals name rule
            matches 1. r2 therefore does not touch the 11 `phone_inside_longer_number` references.
        - **Forms that neither r1 nor r2 covers, checked against the plan's "no other residue"
            claim:**
            - an honorific in capitals followed by a capital (`SR. P…`): 0;
            - a lower-case honorific followed by a capital (`sr. P…`): 0;
            - `+34` with a separator or bracket followed by a full nine-digit number: 0. The 8
                descriptions with a `+34 6…` prefix hold no complete number after it;
            - `señor/señora/señorita` + a capitalised word: 3, all preceded by "Nuestra"
                (place and church names, not people); 0 in a contact context.
        - These counts confirm the plan's scope. They do not prove that no other personal-data
            form exists; that is the residue scan's limit.
- **Unverified assumptions:** none that changes the outcome. The equality check and the residue
    rerun after the backfill are the falsification points the plan already names.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | Success criteria are residue counts, the version column and the equality check; M2 removes one criterion that could not fail. |
| Repository and dependency assumptions match reality | Pass after correction | M1: the image comes from `main`, and the tfvars file is not tracked. |
| Supporting evidence is reproducible and sufficient | Pass | DuckDB spike and Athena counts reproduced above. |
| Design is the simplest adequate option | Pass | One macro body and one constant; no layer, no side-by-side versions. |
| TDD slices and validation commands are executable | Pass | Each RED names a test file, an input and the expected failure; commands exist in CI. |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | Synthetic fixtures; history reaches r2 only by backfill; old snapshots kept by an explicit, owned decision. |
| Operations, recovery, migration, and cost are addressed | Pass after correction | M1 orders merge → image → apply → backfill; recovery is a rerun; cost under $0.10 one-off. |

## Strengths

- r2 is evidence-led: each new alternative maps to a measured residue, and its over-redaction was
    measured before the plan was written.
- It uses the redaction version the repository already reserves for this change, so it needs
    neither a `transformation_version` bump nor a schema change.
- It names the snapshot-expiry trade-off against FEATURE-005's pinning and gives it an owner,
    without deciding it in passing.

## Findings

### 🟡 M1 — The deploy step does not match how the image is built

- **Problem:** Task 2.1 says "build and push the image, set its digest in
    `infra/silver/silver.auto.tfvars`" and lists that file under **Files → Change**. In fact:
    - images are built only by GitHub Actions on a push to `main`, tagged with the merge commit's
        SHA;
    - the tfvars file is gitignored and holds operator values.
- **Evidence:** `docs/silver-rebuild.md:137-139`; `.gitignore:79`;
    `git ls-files infra/silver` does not list the file. The live value names the image of merge
    commit `aec9596`.
- **Impact:** an implementer would either build an image by hand from a feature branch, which
    then runs unreviewed code against the lake, or try to commit an ignored file that holds the
    alert address.
- **Recommendation (applied):**
    - Phase 2 starts only after the Phase 1 PR is merged to `main`, which needs user approval and
        falls inside the GitHub activity hours.
    - The operator then sets `silver_image` to the merge commit's SHA in the local tfvars and
        runs `terraform apply`.
    - The backfill follows the documented procedure.
    - The tfvars file leaves the plan's file list.
- **Disposition required:** fixed in the feature plan and in the technical plan's task 2.1.

### 🟡 M2 — The backfill record lands in another feature's file, and one criterion cannot fail

- **Problem:**
    - Task 2.2 writes its counts into "the FEATURE-005 findings document". That file belongs to
        FEATURE-005 task 3.2, does not exist yet, and lives on another feature's branch.
    - Its criterion "`redacted_phone` and `redacted_name` ≥ their r1 values" is always met,
        because the backfill also covers the days collected since 2026-10-31.
- **Evidence:** the FEATURE-005 technical plan scopes `docs/data-quality/silver-quality-2026-11.md`
    to its task 3.2. Collection resumed on 2026-11-01, with 140 complete walks that day.
- **Impact:** cross-feature edits cause merge coupling, and a check that cannot fail proves
    nothing.
- **Recommendation (applied):**
    - Task 2.2 records the counts in `dev/plans/implementations/FEATURE-006-redaction-r2.md`,
        the repository's place for post-implementation notes, and closes the plan. FEATURE-005's
        findings may cite that note.
    - The ≥ criterion is replaced by one that can fail: no row of the current `listing_text`
        has a `redaction_version` other than `r2`, and the residue classes hit their targets.
- **Disposition required:** fixed in the feature plan and in the technical plan's task 2.2.

### 🟢 L1 — Keep the window between apply and backfill short

- **Evidence:** between the image apply and the backfill, incremental runs rewrite only the
    listings they touch. `listing_text` then mixes r1 and r2 rows. Each row's version is
    truthful, but the old phone number stays readable until the backfill.
- **Suggestion:** run the apply and the backfill in one operator session. Or set
    `trigger_enabled = false` in the same apply and re-enable it after the backfill.
- **Benefit:** the residue is gone sooner, and the snapshot history is easier to read.

### 🟢 L2 — `Señor` + capitalised word is a known non-gap

- **Evidence:** 3 matches on the reviewed snapshot, all "Nuestra Señora …".
- **Suggestion:** do not add `señor/señora` to the honorific list in r2. If a later scan finds
    one in a contact context, add it as r3.
- **Benefit:** avoids redacting place and church names.

## Alternatives considered

- **Redact in Python with look-around** — exact digit boundaries. Trade-off: a new layer and a
    second regex engine for one case RE2 handles. Verdict: stick with the plan.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| The boundary-free `34` over-redacts a reference | Low (1 of 19,038 today) | Low | Accepted: privacy outranks a lost reference | implementer; the task 2.2 counts |
| Old snapshots keep r1 text in the private bucket | Certain until expiry | Low (private bucket, landing holds raw text anyway) | Expiry decided after FEATURE-005 closes | user |
| An incremental run overlaps the backfill | Low | Medium | Documented procedure disables the event source mapping | operator; ECS task list |
| Landing lacks a run's text, so the backfill cannot redact history | Very low | High | The equality check compares counts per table against a local backfill | operator; a count mismatch |

## Effort check

- **Plan estimate:** S (~3h)
- **Reviewer estimate:** S (~3.5h) — confidence High
- **Why it differs / hidden complexity:** two approval-gated PRs (Phase 1, then the backfill
    record) and the wait for the image build.

## Reuse & conflicts

- **Reuse:**
    - `docs/silver-rebuild.md`: the manual backfill and equality-check procedures, unchanged.
    - `analysis/silver-quality/06_redaction.sql`, from the FEATURE-005 integration branch: the
        residue scan, run privately with new snapshot ids.
- **Conflict / coordinate with:**
    - `dev/plans/README.md` also gains the FEATURE-005 row on that feature's branch. Whichever
        merges second resolves a one-line conflict.
    - FEATURE-005's task 3.2 should cite the FEATURE-006 implementation note in its redaction
        section.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-006-technical-plan.yaml`
- **Tasks:** 5. The dependency graph is acyclic and verified, and the critical path is
    1.1 → 1.2 → 1.3 → 2.1 → 2.2.
- **TDD contract:** RED failures and commands verified yes. The tasks run in order because they
    share `redact.sql` and the redaction test file.
- **File boundaries:** create and modify paths are all inside the allowed files, with no overlap
    with the forbidden files.
- **Git workflow:**
    - Phase 1 tasks branch from `feature/redaction-r2/integration` and merge back in order; the
        PR to `main` needs user approval.
    - Task 2.1 makes no commit.
    - Task 2.2 branches from `main` after the merge and opens its own PR.
- **Required checks:** `python-lint-and-test`, `workflow-consistency`.

## Approval criteria

- **Blockers (must fix):** none.
- **Required dispositions:** M1 fixed; M2 fixed.
- **Optional:** L1 (adopted in task 2.1 as the recommended order); L2 (recorded, no change).
- **Accepted residual risk:** old Iceberg snapshots keep r1 text until the user decides on
    expiry; over-redaction of rare `34`-prefixed references.
- **Technical plan:** emitted.

## Next step

Run `/implementer FEATURE-006`.
