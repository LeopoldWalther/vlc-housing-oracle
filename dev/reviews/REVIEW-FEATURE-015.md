# Review — FEATURE-015: Run guards: a Silver run refuses what it cannot do correctly

**Reviewer:** `reviewer` · **Date:** 2026-11-23 · **Plan:** [FEATURE-015](../plans/FEATURE-015-run-guards.md)
**Verdict:** ✅ Approved

## Summary

The outcome is feasible and every claim the design rests on holds in the code. Six corrections
were needed, all local and unambiguous from the repository: where the guard is called so that it
also covers a start with nothing pending, the order of commit listing and guard, an attribute the
plan's spike reported and a rebuilt check does not find, a fallback the plan left to the
implementation, the mail legend that Silver shares with the labeller, and the cause printed with
a refusal. They are folded into the technical plan and noted in the feature plan. The three
owner questions are taken as the plan recommends; the owner can overrule them before task 3.2.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-015-run-guards.md`; FEATURE-004 is complete
  (`dev/plans/README.md`). FEATURE-016, 017, 020, 023 and 024 name this feature as a need or a
  neighbour and none is in progress.
- **Code, tests, data, and configuration:**
  - `src/silver/rebuild.py:43-44`, `:236-323`, `:328-339`, `:742-743`, `:779-868`: versions,
    `process_landing`, the snapshot properties, `main`. `main` returns "nothing pending"
    without calling `process_landing`, lists commits before its `try` block, and maps four
    error classes to exit 1.
  - `src/silver/ingest.py:24-63`, `:66-97`: the log stores `transformation_version`,
    `ingested_runs` does not return it, `record_ingested` upserts one row per commit. A backfill
    passes every commit, so it rewrites every row.
  - `src/silver/sink.py:98-125`: an upsert is a delete and an append in one transaction, both
    with the run's properties; `:26-52` an overwrite carries them too. Every writer of
    `listing_text` therefore leaves `redaction_version` on its current snapshot.
  - `src/silver/landing.py:124-150`, `:179-202`: only files of the runs named per table are
    opened; the count is checked for pending runs; `duckdb.IOException` is relabelled.
  - `src/silver/commits.py:97-110`: a pending run enters a table's run set only when its commit
    names a day for that table.
  - `contracts/landing/v3/README.md:37-38`, `:71`: one file per table and run, zero rows
    included; `observed_dates` lists the days the run's rows touch. All four tables carry
    `observed_date` (`contracts/landing/v3/schema.json`).
  - `src/silver/consumer.py:105-111`, `infra/silver/queue.tf:22-26`,
    `infra/silver/alerts.tf:54-99`, `infra/silver/ecs.tf:192-199`: any non-zero exit raises,
    three receives, one mail per stopped task, the task runs `--mode incremental`.
  - `tests/test_silver_cli.py:179`, `:245`, `:305` expect exit 1 for the three refusals;
    `tests/test_silver_consumer.py:102-104` pins the retry of exit 1.
- **Evidence reviewed:** rebuilt the plan's spike with DuckDB 1.5.6 in the project's
  environment. A file of garbage bytes raises `duckdb.InvalidInputException` ("No magic bytes"),
  as the plan says. A missing local file raises `duckdb.IOException` ("No files found").
  `duckdb.HTTPException` is a subclass of `duckdb.IOException`; an instance built by hand has
  **no** `status_code` attribute, though one can be set (see M3). Not run: any read against S3.
- **Unverified assumptions:** that every existing commit names exactly its files' days, and that
  the real ingest log and `listing_text` say `silver-v1` and `r2`. Neither can be checked without
  real data; no local copy of landing exists. Owner: the operator, in task 4.2, before the final
  pull request is merged.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | Seven success criteria, each a test or an exit code |
| Repository and dependency assumptions match reality | Pass | Every cited line checked; M1 and M3 correct two |
| Supporting evidence is reproducible and sufficient | Pass | Spike rebuilt; M3 corrects one observation; real data in task 4.2 |
| Design is the simplest adequate option | Pass | No new store, column, module or dependency; one exception class |
| TDD slices and validation commands are executable | Pass | Module constants are read at call time, so the patches of 1.1 and 1.2 work |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | M4 (no silent fallback), M6 (no row value in a refusal line) |
| Operations, recovery, migration, and cost are addressed | Pass | M2 (metric on a refusal), M5 (mail legend); $0 a month |

## Strengths

- The guard reads what a run already loads: the ingest log, plus one table's metadata. A state
  store or a table property would have been a second checkpoint to keep consistent.
- A backfill is the only way through the guard and there is no override flag, so the rule of
  `CLAUDE.md` ("a rule change is applied to history by an explicit full backfill") becomes
  something the code enforces.
- The plan corrects the review finding it started from: the filter at
  `src/silver/rebuild.py:267-271` is needed, and the defect is the missing check per file.
- Exit 3 leaves the retry of real failures untouched and is safe in either deployment order.

## Findings

### 🟡 M1 — The guard is not reached when nothing is pending

- **Problem:** task 1.1 puts the guard into `process_landing`; task 3.2 expects exit 3 "also when
  nothing is pending". `main` prints "nothing pending" and returns 0 without calling
  `process_landing`.
- **Evidence:** `src/silver/rebuild.py:834-836`.
- **Impact:** the daily start after a backfill that stopped early would exit 0, which is the one
  signal the plan's first question is about.
- **Recommendation:** one function `require_current_versions(catalog, ingested)` in
  `rebuild.py`. `process_landing` calls it in incremental mode right after it has read the log;
  `main` calls it in incremental mode inside the reporting block, before the "nothing pending"
  branch. Both read the log, as both do today.
- **Disposition required:** fixed in the technical plan (tasks 1.1 and 3.2).

### 🟡 M2 — A refusal by the guard must still publish `PendingRuns`

- **Problem:** the plan moves commit listing and the guard into the reporting block and says
  that a run refused before its commits are known publishes no `PendingRuns`. It does not say
  which of the two comes first.
- **Evidence:** `src/silver/rebuild.py:818-828`, `:863-865`; FEATURE-016 turns `PendingRuns` into
  a heartbeat that treats missing data as breaching.
- **Impact:** with the guard first, a version refusal would publish nothing and raise the
  heartbeat alarm for the wrong reason, while the count of pending runs is exactly what the
  operator needs.
- **Recommendation:** list the commits first, set the count, then run the guard. Only a refusal
  while listing (a commit that is not landing v3) publishes no `PendingRuns`.
- **Disposition required:** fixed in the technical plan (task 3.2).

### 🟡 M3 — `status_code` is not an attribute of every `duckdb.HTTPException`

- **Problem:** the plan says the class and its attribute exist in DuckDB 1.5.6. A rebuilt check
  finds the class, as a subclass of `duckdb.IOException`, and no `status_code` on an instance
  built by hand.
- **Evidence:** `dir(duckdb.HTTPException("x"))` lists `add_note`, `args`, `with_traceback`.
- **Impact:** `error.status_code` would raise `AttributeError` inside an error path.
- **Recommendation:** read it with `getattr(error, "status_code", None)`; the test sets the
  attribute on its instance. The plan's fallback stands: without the attribute a missing S3
  object is a failure and is retried, as today.
- **Disposition required:** fixed in the technical plan (task 3.1).

### 🟡 M4 — The fallback of the days check is a design decision, not an implementation detail

- **Problem:** if real history holds a commit whose days differ from its file, the plan lets
  task 4.2 fall back to a weaker rule. A backfill reads every commit, so the recovery path
  itself would be refused after deployment.
- **Evidence:** the first open assumption of the plan; `src/silver/rebuild.py:239` (a backfill
  ingests nothing as known).
- **Impact:** an Implementer choosing the weaker rule would change the contract check without a
  decision.
- **Recommendation:** the dry run on real landing is its own task before the final pull request.
  If it is refused, the Implementer stops and returns the finding, with table, run and days,
  to the owner. Nothing is merged and no rule is weakened without that decision.
- **Disposition required:** fixed in the technical plan (task 4.2).

### 🟡 M5 — The mail legend is shared with the labeller

- **Problem:** task 4.1 lets the legend "name 1 and 3". The same line explains the labeller's
  exit code 1, which this feature does not change.
- **Evidence:** `infra/silver/alerts.tf:93`.
- **Impact:** a legend that redefines 1 as "failed" would mislabel every labeller refusal.
- **Recommendation:** the legend says per task what a code means: Silver 1 failed and is
  retried, Silver 3 refused and is not retried, labeller 1 as today.
- **Disposition required:** fixed in the technical plan (task 4.1).

### 🟡 M6 — A refusal line must not print the cause's message

- **Problem:** task 3.2 prints "the cause's type"; the design says read failures need no wrapper
  because "DuckDB's message already names the file". A DuckDB message can also hold a value.
- **Evidence:** the plan's constraint "never another row value"; a conversion error quotes the
  value it could not convert.
- **Impact:** a listing's value in the task log, which the repository's rules forbid.
- **Recommendation:** a refusal line prints the refusal's own message and the type name of its
  cause, nothing of the cause's text. A failure (exit 1) keeps Python's traceback, as an
  unhandled error does today.
- **Disposition required:** fixed in the technical plan (task 3.2).

### 🟢 L1 — The census reader loses its catch-all

- **Evidence:** `src/silver/rebuild.py:482-483` turns any `duckdb.Error` into a refusal; task 3.1
  narrows that to a missing and a non-Parquet file.
- **Suggestion:** give the census reader the local existence check that landing's reader has, so
  the existing test of a missing directory keeps its refusal without matching message text.
- **Benefit:** no test depends on DuckDB's wording. Folded into task 3.1.

## Alternatives considered

- **A `redaction_version` column in the ingest log** — one place for both versions. Trade-off:
  an upsert cannot add a column, so it needs a backfill to exist. Verdict: stick with the plan;
  the snapshot summary of `listing_text` holds the fact today.
- **A read-only check of real landing before any code is written** — would settle the first
  assumption early. Trade-off: it needs a throwaway script and a copy of real data for a check
  the branch's own backfill performs anyway. Verdict: keep the dry run as task 4.2, before the
  final pull request, with M4's stop rule.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| Real history holds a commit whose days differ from its file | Low | High | Task 4.2 before the final pull request; stop and decide (M4) | Operator; the dry run exits 3 |
| A backfill under a new rule version stops early and the image is rolled back | Low | Med | Runbook: finish a started backfill under the same image; FEATURE-020 closes it | Operator |
| One refusal mail per start between a deployment and its backfill | High | Low | Accepted with the plan's first two questions | Owner; the mail names exit 3 |
| FEATURE-016 edits `main` and `consumer.py` too | High | Low | This feature lands first; FEATURE-016 rebases | Reviewer of FEATURE-016 |

## Effort check

- **Plan estimate:** M (~9h)
- **Reviewer estimate:** M (~10.5h) — confidence Med
- **Why it differs / hidden complexity:** the restructured `main` (M1, M2) touches every command
  test's path, and the dry run is a task of its own.

## Reuse & conflicts

- **Reuse:** `tests/test_silver_rebuild.py:82-83` already reads both versions from a snapshot
  summary; `src/silver/landing.py:188-190` is the existence check task 3.1 copies.
- **Conflict / coordinate with:** FEATURE-016 (`rebuild.py` `main`, `consumer.py`, `alerts.tf`),
  FEATURE-017 (the labeller's exit codes stay as they are), FEATURE-020 (must keep
  `redaction_version` on `listing_text`'s current snapshot), the published architecture page.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-015-technical-plan.yaml`
- **Tasks:** 10; dependency graph and critical path verified yes
- **TDD contract:** RED failures and commands verified yes, against today's behaviour
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `terraform-validate`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** M1 to M6, all fixed in the technical plan
- **Optional:** L1, folded into task 3.1
- **Accepted residual risk:** the two assumptions about real data until task 4.2; one mail per
  refused start until FEATURE-016; an image rollback after a half-finished backfill until
  FEATURE-020
- **Technical plan:** emitted

The plan's three questions are answered as it recommends: a start with nothing pending is
refused on a version mismatch; a refusal mails once per start and raises no dead-letter alarm;
no digest of rule inputs is pinned here. The owner can overrule each before task 3.2 starts.

## Next step

Run `/implementer FEATURE-015`.
