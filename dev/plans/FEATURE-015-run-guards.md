# FEATURE-015 — Run guards: a Silver run refuses what it cannot do correctly

**Status:** 🔵 Planned · **Effort:** M (~9h) · **Priority:** High
**Branch root:** `feature/run-guards` · **Created:** 2026-11-22 · **Updated:** 2026-11-23

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-015.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-015-technical-plan.yaml`.

## Objective

An incremental Silver run stops before its first write when the code's rule or redaction version
differs from the version that wrote the tables, or when a landing file holds other days than its
commit names. A refusal and a failure get different exit codes, so the queue retries only what a
retry can cure. Today all three cases end with exit 0 or with the wrong label.

## Context

- **Current state:**
    - `TRANSFORMATION_VERSION` and `REDACTION_VERSION` (`src/silver/rebuild.py:43-44`) are stamped
        on rows (`:742-743`, `src/silver/sql/listing_text.sql:13-14`) and on every snapshot
        summary (`src/silver/rebuild.py:328-339`). The ingest log stores the rule version per run
        (`src/silver/ingest.py:43`, `:84`) and is written last (`src/silver/rebuild.py:317`).
    - A run reads the ingest log at its start (`src/silver/rebuild.py:240`, `:818`), but
        `ingested_runs` drops the version (`src/silver/ingest.py:48-63`). The AWS task always
        runs `--mode incremental` (`infra/silver/ecs.tf:196`).
    - Per pending file, landing's reader checks columns and the commit's row count
        (`src/silver/landing.py:181-202`). The commit's `observed_dates` decide which files are
        opened (`src/silver/commits.py:97-110`) and which days are replaced.
    - The command prints a refusal's message and returns 1 (`src/silver/rebuild.py:853-860`).
        The consumer raises on any non-zero exit (`src/silver/consumer.py:104-110`), the queue
        redelivers up to three times (`infra/silver/queue.tf:23-26`), and every non-zero exit
        mails (`infra/silver/alerts.tf:57-68`).
- **Problem (review of 2026-11-22, items A2, B8 and F fifth bullet, each checked in the code):**
    - **A2, confirmed.** Nothing compares the versions. An incremental run under a new version
        rewrites only the days and listings its commits touch and exits 0. FEATURE-006 accepted
        that window in writing (`dev/plans/FEATURE-006-redaction-r2.md:90-91`).
    - **B8, confirmed, but the cause is not the filter.** The filter at
        `src/silver/rebuild.py:267-271` is needed: files of ingested runs are read whole and hold
        other days, which `replace_days` would refuse (`src/silver/sink.py:72-77`). The defect is
        that no check compares a pending file's days with its commit, so a row dated outside
        them is dropped by that filter. Found on the way: a pending file is never opened for a
        table whose commit names no day, although the contract promises one file per table and
        run (`contracts/landing/v3/README.md:37-38`); its count is not checked either.
    - **F, confirmed in part.** `duckdb.IOException` becomes `LandingContractError`
        (`src/silver/landing.py:196-197`) and any `duckdb.Error` becomes `ReferenceDataError`
        (`src/silver/rebuild.py:482-483`); the cause is chained but never printed. Not confirmed:
        `SilverSchemaError` keeps the cause's text (`src/silver/sink.py:171-175`), and
        `stored_answers` relabels nothing (`src/enrichment/text_labels.py:280-291`). Found on the
        way: commits are parsed before the block that reports refusals
        (`src/silver/rebuild.py:817-826`, block from `:830`), so a commit that is not landing v3
        ends in a traceback; and a file that is not Parquet raises
        `duckdb.InvalidInputException`, which no clause catches.
- **Constraints:** no new resource, no new dependency, no schema change. The consumer stays one
    file of standard library and boto3 (`infra/silver/queue.tf:53-58`). Messages name tables,
    runs, days, versions and paths, never another row value.

## Scope

- **In scope:** the version guard of incremental runs; the days check of pending landing files;
    read failures told apart from refusals; exit code 3 for a refusal and a consumer that does
    not retry it; the runbook, the mail's legend and the architecture page made true again.
- **Out of scope:** the labeller's version filter and exit codes (FEATURE-017); new alarms, the
    dead-letter text and telemetry (FEATURE-016); a way past a refused commit (FEATURE-023); one
    consistency point across tables (FEATURE-020, see the first risk); a pinned digest of rule
    inputs (see the questions). No rule changes what it writes, so there is **no new
    `transformation_version` and no backfill**.
- **Users / consumers:** the operator, through exit code, log line and mail.

## Dependencies

- **Needs:** FEATURE-004 — complete. Nothing else.
- **Unblocks:** FEATURE-016 (its mail text and telemetry build on the exit codes; both touch
    `consumer.py` and `alerts.tf`, so this one lands first), FEATURE-017 (labeller half of A2),
    FEATURE-023 (the days check makes refusals likelier), FEATURE-024 (r3 relies on the guard),
    FEATURE-020 (its maintenance must keep the summary property the guard reads).

## Open assumptions

- **Assumption:** every existing commit names exactly its files' days. — **Evidence:** the writer
    derives the list from the files it has written, in one code path (read in the acquisition
    repository); unvalidated on real data. — **If wrong:** the next backfill is refused. Task 4.2
    finds out first, on a local copy; the fallback refuses only a file's day the commit lacks.
- **Assumption:** every row of the real ingest log says `silver-v1` and the current snapshot of
    `listing_text` says `r2`. — **Evidence:** the rule version never changed (`git log -S`), and
    FEATURE-006 recorded `r2` after its backfill. — **If wrong:** the first run is refused; task
    4.2 reads both before the deployment.
- **Assumption:** DuckDB reports a missing S3 object as `duckdb.HTTPException` with
    `status_code` 404. — **Evidence:** the class and attribute exist in DuckDB 1.5.6 (spike);
    unvalidated against S3. — **If wrong:** a missing file counts as a failure and is retried
    three times, which is today's behaviour.

## Design

- **Primary approach:** three checks before the first write, one mapping of outcome to exit code.
    - **Rule version: the ingest log.** It is the checkpoint, already read whole at every start,
        and a completed backfill rewrites every row of it. `IngestedRun` gains
        `transformation_version`; the guard refuses when any row differs from the constant. No
        extra read, no migration.
    - **Redaction version: the current snapshot summary of `listing_text`.** The log has no
        such column, and an upsert never adds one (`src/silver/sink.py:112-113`).
        `listing_text` is the only redacted table, its summary carries the version today, and a
        backfill replaces it in one transaction. One metadata read; a summary without the
        property is refused as unknown.
    - **A backfill is the way through.** `--mode backfill` skips the guard, rewrites every table
        and writes the log last. A backfill that stops early leaves the old version in the log,
        so incremental runs stay refused until one completes. There is no override flag.
    - **Days.** The query that counts a pending file's rows also returns its distinct days;
        both must equal the commit. Every pending run's four files are checked, also for a
        table without days. A null day counts as a day the commit does not name.
    - **Outcomes.** 0 done or nothing pending; 1 failed (an unhandled error, with traceback and
        chained cause); 2 usage; 3 refused: the four error classes of today and the new
        `BackfillRequired`. A missing or non-Parquet file is a refusal; every other read error
        propagates unchanged.
    - **Consumer.** Exit 3 returns normally, so the batch is deleted and nothing is redelivered.
        The mail still follows, once instead of three times. The refused runs stay pending.
- **Boundaries and data flow:** `ingest.py` reports, `rebuild.py` decides, `landing.py` checks
    files, `consumer.py` maps the exit code. No new module.
- **Contracts and invariants:** no run adds rows of a second rule or redaction version to a
    table. A run refused by the guard or a landing check writes nothing. Exit codes are a public
    contract of the command; 1 changes its meaning from "refused" to "failed".
- **OOP / SOLID:** one exception class; pure functions otherwise. **Patterns:** None.
- **Deliberately rejected complexity:** a `redaction_version` column in the log (needs schema
    evolution in an upsert and a backfill to fill it); the versions in table properties or a
    state store; a `--force` flag; one exit code per refusal kind; a wrapper exception for read
    failures (DuckDB's message already names the file); a scan of `listing_text` in every run.

## Approach

### Phase 1 — Version guard

- [ ] **1.1 Rule version.** RED in `tests/test_silver_incremental.py`: process all commits but
    the last, patch `src.silver.rebuild.TRANSFORMATION_VERSION` to `silver-v2`, process
    incrementally; assert `BackfillRequired` naming both versions and unchanged snapshot ids.
    Fails today: the run returns and `observation` holds both versions. GREEN:
    `transformation_version` on `IngestedRun` (first pinned in `tests/test_silver_ingest.py`)
    and the guard in `process_landing`, before the pending set. Then pin: a backfill under the
    patch passes and the next incremental run too; a backfill whose `record_ingested` raises
    leaves incremental runs refused.
- [ ] **1.2 Redaction version.** RED: the same with `REDACTION_VERSION` patched to `r3`. Fails
    today: `listing_text` holds `r2` and `r3` rows. Second case: a `listing_text` whose current
    summary lacks the property is refused as unknown. GREEN: read the summary in the guard.

### Phase 2 — Days of a pending file

- [ ] **2.1 A file's days equal its commit's.** RED in `tests/test_silver_incremental.py`: remove
    one day from `observed_dates.observation` of a synthetic commit and process; assert
    `LandingContractError` naming table, run and day, and no table written. Fails today: the run
    returns without that day's rows. Also a commit naming a day its file lacks. GREEN: the
    commit's days reach `_check_v3_file` the way its counts do; unit cases in
    `tests/test_silver_landing_v3.py`.
- [ ] **2.2 Every pending file is checked.** RED: a commit with no day and count 0 for `walk`
    over a file with rows. Fails today: the file is never opened. GREEN: check the four files
    of every run that has a commit; the views stay over the named runs.

### Phase 3 — Refused or failed

- [ ] **3.1 Read errors stay read errors.** RED in `tests/test_silver_landing_v3.py`: a
    connection double raising `duckdb.IOException` gives that exception, not
    `LandingContractError`; a `duckdb.HTTPException` with `status_code` 404 gives "has no file";
    a file of garbage bytes gives "is not a Parquet file". Fails today: relabelled, relabelled
    without the reason, uncaught. The same three for `read_census_sections`. GREEN: one function
    in `landing.py` that names the defect of a read error or returns nothing, used at both sites.
- [ ] **3.2 Exit code 3.** RED in `tests/test_silver_cli.py`: the three refusal tests expect 3
    (`:179`, `:245`, `:305`); a commit of another version exits 3 without a traceback; a version
    mismatch exits 3 with `backfill` on stderr, also when nothing is pending; a refusal with a
    cause prints the cause's type; a patched `duckdb.IOException` exits 1. GREEN: `EXIT_REFUSED`,
    commit listing and the guard inside the block, the cause in the line. A run refused before
    its commits are known publishes no `PendingRuns`.
- [ ] **3.3 The consumer does not retry a refusal.** RED in `tests/test_silver_consumer.py`:
    `stopped(3)` returns `{"task": ..., "exit_code": 3}`; fails today with `TaskFailed`. A
    second test pins the consumer's constant equal to `EXIT_REFUSED`. GREEN: one branch in
    `_outcome` and one log line. Exit 1 still raises (`:102-104`).

### Phase 4 — Documents and deployment

- [ ] **4.1 Documents.** `docs/silver-rebuild.md`: exit-code table, modes, alarm table,
    dead-letter steps, and "a new version" in the backfill section. `infra/silver/alerts.tf`: the
    legend names 1 and 3 and who is retried. `docs/adr/0002-incremental-processing.md`, decision
    5: one sentence. `docs/architecture.html`: the weakness "No guard on rule versions" goes.
    Validation: `terraform fmt -check` and `validate`, leak guard, workflow validator.
- [ ] **4.2 Dry run and deployment (operator; needs the owner's approval; upper bound $0.10).**
    Before the merge: a local backfill of a fresh copy of landing with this branch, as the
    runbook's equality check does, must exit 0; read the log's versions and the summary of
    `listing_text`. After the merge: set `silver_image`, `terraform apply` (task definition,
    consumer, mail text). Validation: the next run exits 0. No backfill in AWS.

## Files

- **Change:** `src/silver/ingest.py` — `transformation_version` on `IngestedRun`.
- **Change:** `src/silver/rebuild.py` — guard, `BackfillRequired`, `EXIT_REFUSED`, the block.
- **Change:** `src/silver/landing.py` — days check, every pending file, read-error function.
- **Change:** `src/silver/consumer.py` — exit 3 is not retried.
- **Change:** `infra/silver/alerts.tf` and the three documents of task 4.1.
- **Tests:** `tests/test_silver_{incremental,ingest,landing_v3,cli,consumer}.py`.

## Test strategy

- **Unit:** guard with equal, differing and unknown versions and an empty catalog; days equal,
    missing, extra and null; the three read-error classes; the consumer's exit paths.
- **Contract / fixtures:** synthetic landing only; a commit is altered in the temporary tree.
- **Integration:** incremental-equals-backfill stays green; the command through a subprocess.
- **Retries / evolution:** a refused run changes no snapshot; older tables pass the guard.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`, `ruff check` and
    `ruff format --check` on `src tests dev`, `python dev/tools/check_leaks.py`,
    `python dev/tools/validate_workflow.py`, `terraform -chdir=infra/silver validate`.
    **Manual:** task 4.2.

## Operational and compliance impact

- **Data sourcing / legal:** N/A; the landing contract is read more strictly, not changed.
- **Privacy / security:** text of two redaction versions no longer shares a table. Log lines
    gain file paths and error types, no row value.
- **Observability:** exit 3 against exit 1 in the failure mail; the cause in the task log.
- **Failure and recovery:** a refusal is not retried and no longer reaches the dead-letter
    queue; each later start (a commit or the daily catch-up) meets it again and mails once. A
    failure is retried as today. After a version change: deploy, then backfill.
- **Migration / compatibility:** either deployment order is safe: an old consumer retries exit
    3 as it retries 1 today. Scripts that read exit 1 as "refused" must read 3.

## Estimated monthly cloud cost

$0/month — no resources added or changed. A refusal now costs one Fargate start instead of
three. One-off: one read of landing for the dry run and one apply, under $0.10.

- **Cost drivers & cheaper alternatives:** none; the guard reads what the run already loads plus
    one table's metadata. Nothing is cheaper than no resource; a state store was rejected.
- **External / non-AWS costs:** none. **Budget check:** yes.

## Success criteria

- [ ] An incremental run under another rule or redaction version exits 3, names both versions and
    `backfill`, and changes no snapshot; a completed backfill clears it.
- [ ] A pending file whose days differ from its commit is refused in both modes.
- [ ] A read error other than a missing or non-Parquet file exits 1 with its cause in the log.
- [ ] The consumer returns on exit 3 and raises on every other non-zero exit.
- [ ] Runbook, mail legend, ADR 0002 and the architecture page describe this behaviour.
- [ ] Every behavior was implemented test-first; suites, lint, format, `terraform validate`, the
    leak guard and the workflow validator pass.
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced.

## Open questions & risks

- **Question:** refuse a run with nothing pending when the versions differ? Recommendation: yes.
    It is the only signal of a backfill that stopped early, at most one mail a day.
- **Question:** accept that a refusal mails once per start and never raises the dead-letter
    alarm, until FEATURE-016 adds its alarms? Recommendation: yes.
- **Question:** pin a digest of the rule inputs (SQL, thresholds, registry) in a test, so a rule
    change without a version decision fails CI? The guard sees only a version that was raised,
    and the rule version never was. Recommendation: not here; decide it with FEATURE-022.
- **Risk:** a backfill under a new rule version stops early and the image is rolled back; the
    log still says the old version and the guard passes on half-migrated tables. — *Mitigation:*
    the runbook says to finish a started backfill under the same image; FEATURE-020 closes it.
- **Risk:** the days check refuses history that Silver ingested long ago. — *Mitigation:* the dry
    run of task 4.2 on a local copy, before any deployment.
- **Risk:** maintenance that writes a snapshot to `listing_text` without the property makes the
    guard refuse. — *Mitigation:* it fails closed and says why; noted for FEATURE-020.

## Progress log

- **2026-11-22** — Plan created from the architecture review of 2026-11-22 (A2 Silver half, B8,
    F fifth bullet). Spike: DuckDB 1.5.6 raises `InvalidInputException` for a non-Parquet file.
- **2026-11-23** — Review approved with corrections M1 to M6, which the technical plan carries:
    the guard is one function called by `process_landing` and by `main` (so a start with
    nothing pending is covered); commits are listed before the guard, so its refusal still
    publishes `PendingRuns`; `status_code` is read with `getattr`, because a rebuilt check found
    no such attribute on a hand-made `HTTPException`; a refused dry run goes back to the owner
    instead of weakening the days check; the mail legend keeps the labeller's exit code 1; a
    refusal line prints the type of its cause, never its text. The three questions are answered
    as recommended.
