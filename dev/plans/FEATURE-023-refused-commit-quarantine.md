# FEATURE-023 — A way past a refused commit, and a writer lease

**Status:** 🔵 Planned · **Effort:** L (~16h) · **Priority:** Medium
**Branch root:** `feature/refused-commit-quarantine` · **Created:** 2026-11-22 · **Updated:** 2026-11-22

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-023.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-023-technical-plan.yaml`.

## Objective

An operator can set one landing run that breaks the contract aside with a write-once record, so
the runs after it proceed and the gap is visible in the ingest log. Every command that writes
Silver takes a lease first, so a second writer is refused wherever it was started. Today one bad
commit stops Silver for good, and only the trigger's topology keeps two writers apart.

## Context

- **Current state:**
    - Pending work is one batch, not a lane of single commits: the commits whose run id the
        ingest log lacks (`src/silver/commits.py:76-85`, `src/silver/rebuild.py:240-241`,
        `:818-826`). Their sort by start only orders the report. Every day the batch touches is
        rebuilt from every known run that touches it (`rebuild.py:249-260`, `commits.py:97-110`).
    - Every landing check runs before the first write (`rebuild.py:254-260`). One failing file
        refuses the whole batch with exit 1 (`rebuild.py:853-860`), the consumer raises
        (`src/silver/consumer.py:104-110`) and the third receive raises the dead-letter alarm
        (`infra/silver/queue.tf:23-26`). Each later start finds the same set and is refused.
    - Starts are serialized by a FIFO queue with one message group and one consumer
        (`queue.tf:17-27`, `:168-173`), which adopts a running task of the family
        (`consumer.py:65`, `:75-81`). The command itself checks nothing (`rebuild.py:778-864`).
- **Problem (review of 2026-11-22, items B5 and B7, each checked in the code):**
    - **B5, confirmed.** Nothing subtracts a run, by decision: "Silver never skips a run"
        (`dev/plans/FEATURE-004-incremental-silver-from-landing-v3.md:270-272`, `:486-487`).
        Landing is never rewritten (`contracts/landing/v3/README.md:39`, `:44`), and the runbook's
        "fix the cause" names no means (`docs/silver-rebuild.md:770`). The ways out are a deleted
        landing object, a hand-made log row or a weakened check; none leaves a record.
    - **B5, found on the way.** The operator cannot always tell which run to set aside. Three
        checks over a view of several runs name a table and a count (`src/silver/landing.py:86`,
        `:97-99`, `:119-121`); a commit is parsed under the id in its body, not its key
        (`commits.py:45`, `rebuild.py:770-774`); a body that is not JSON is a traceback (`:205`).
    - **B7, confirmed.** Three entry points pass the queue: a manual `run-task` started while the
        consumer's task runs, kept apart only by a mapping disabled by hand
        (`docs/silver-rebuild.md:717-740`); the documented Glue catalog entry for a laptop
        (`docs/silver-rebuild.md:91-96`), which `ListTasks` cannot see; and FEATURE-020's
        maintenance command. ADR 0003 still says concurrent runs "tolerate each other's commit"
        (`docs/adr/0003-immutable-bronze-and-landing-per-run.md:41-42`). They do not: on a SQLite
        catalog with PyIceberg 0.12.0 a stale second append printed `retrying (1/4)` and
        succeeded (spike repeated on 2026-11-22; first in the FEATURE-004 plan, `:121-127`).
    - **B7, corrected in one point.** The finding counts on an existing conditional-write
        helper. There is none: the labeller checks, then writes
        (`src/enrichment/text_labels.py:317-328`). The pinned `boto3` 1.43.114 has `IfNoneMatch`
        and `IfMatch` on `PutObject` (service model read locally).
- **What the code settles about skipping a run:**
    - A run's walks and sightings leave together, so `listing_event` sees a run in which nobody
        looked: no evidence day and no sighting (`src/silver/sql/listing_event.sql:13-40`).
        `listing_price` loses sightings, a limit the runbook states
        (`docs/silver-rebuild.md:298-299`). Every row stays a true function of what Silver
        holds. Skipping one table of a run would not: walks without sightings make disappearances.
    - A repair arrives as a new run id and is an ordinary late run: its days are rebuilt from
        every run, events and prices are recomputed whole (`rebuild.py:302-316`). No backfill.
- **Constraints:** no new AWS resource and no new dependency. The task role already reads, writes,
    deletes and lists under `silver/` and reads the Glue database (`infra/silver/ecs.tf:115-127`,
    `:136`). The ingest log is written by a strict upsert (`src/silver/sink.py:112-113`).

## Scope

- **In scope:** refusals that name their run; a quarantine record written by an operator command;
    pending work and day rebuilds without quarantined runs, in both modes; the quarantine in the
    ingest log; a writer lease for every command that writes Silver; ADR 0003, the runbook and
    the two public pages made true.
- **Out of scope:** how the acquisition repository lands a corrected run (planned there);
    automatic quarantine (see the questions); a command that lifts a quarantine; exit codes
    (FEATURE-015 owns them) and alarm texts (FEATURE-016); a lease for the labeller, which writes
    no Silver table. No rule changes what it writes from the runs it reads: **no new
    `transformation_version` and no backfill.**
- **Users / consumers:** the operator; readers of `landing_ingest`.

## Dependencies

- **Needs:** FEATURE-015 — its refusal exit code, `BackfillRequired` and the commit listing
    inside the reporting block. It lands after FEATURE-020 (both change `record_ingested`; its
    maintenance command takes the lease), FEATURE-021 (`LANDING_VERSION` in the record's key) and
    FEATURE-022, which moves `process_landing` and `main`; the citations here are today's.
- **Unblocks:** FEATURE-020's maintenance on a schedule, without a trigger disabled by hand.

## Open assumptions

- **Assumption:** S3 refuses a `PutObject` whose `If-None-Match: *` or `If-Match: <etag>` fails
    (412, or 409 during a concurrent write) and honours `If-Match` on `DeleteObject`. —
    **Evidence:** the members are in the installed service model; ADR 0003 relies on the first
    in production; no AWS call was made. — **If wrong:** the drill of 4.2 shows it. Without
    `If-Match` there is no takeover: a stale lease is deleted by hand.
- **Assumption:** every column of the real ingest log is optional, and adding one changes
    metadata only. — **Evidence:** spike on a SQLite catalog: no required field, no new snapshot,
    old rows read null, the present upsert writes a row with null commit fields. — **If wrong:**
    task 4.2 reads the real schema first; the fallback row holds the record's instant as start.
- **Assumption:** no write step lasts 300 s, and writers' clocks differ by under a minute. —
    **Evidence:** whole runs took 30 s to 84 s
    (`dev/plans/implementations/FEATURE-008-listing-quality.md:30-33`). — **If wrong:** a waiting
    writer takes over and the slow one stops at its next renewal, as after a crashed run.
- **Assumption:** the acquisition repository can land a corrected run under a new run id. —
    **Evidence:** none; no such path was found there. — **If wrong:** the gap stays.

## Design

- **Primary approach:**
    - **Control location.** `<Silver location>/_control/`, where the Silver location is the
        namespace's `location` (`infra/silver/main.tf:7`) or, for a SQL catalog, its `warehouse`.
        It is derived, not passed: the backfill's hand-typed override
        (`docs/silver-rebuild.md:736`) would run without a lease when a flag is forgotten.
    - **Quarantine record.** `_control/quarantine/landing=<version>/<run_id>.json` with the run
        id, the landing version, a reason and `quarantined_at`; the version is in the key because
        v3 and v4 share run ids. `python -m src.silver.quarantine --landing <location> --catalog
        <name> --run-id <id> --reason <text>` writes it with `IfNoneMatch="*"`, a local file by
        exclusive creation. An existing record and a run id without a commit in landing are
        refused with FEATURE-015's code. Silver's task role is denied the write.
    - **A whole run or nothing.** A run lists the records once and drops those ids from the
        commits by key, before a commit is read, in both modes. A quarantined run is never
        pending and never among the runs a day is rebuilt from. A record for a run the log holds
        as processed raises `BackfillRequired`: its rows leave by a backfill, not by drift.
    - **The log shows it.** `landing_ingest` gains one optional last column, `quarantined_at`. A
        quarantined run's row carries it, `ingested_at`, the rule version and empty day lists;
        its commit fields are null, since the commit was not trusted. `ingested_runs` returns
        processed rows only. A run writes the row of every record the log does not show yet, also
        when nothing else is pending; a backfill writes them all. `record_ingested` adds the
        column to a log that predates it: null is the true value of every older row. A record
        deleted by hand makes the run pending again, and processing it replaces its row.
    - **Lease.** `_control/writer-lease.json` holds a random token, the command, `acquired_at`
        and `expires_at`, 300 s ahead. Acquire: `PutObject` with `IfNoneMatch="*"`; if the object
        exists and `expires_at` lies more than 60 s back, replace it with `IfMatch` on its ETag,
        else raise `WriterLeaseHeld`. The run renews with `IfMatch` before each write step; a
        failed renewal raises `WriterLeaseLost` and the run stops before that write. Release
        deletes with `IfMatch` in a `finally`.
    - **Where and how it ends.** The lease is taken before the ingest log is read, so it spans
        read, compute and write. A held or lost lease prints one line and exits 1, FEATURE-015's
        code for what a retry can cure. A killed writer blocks others for six minutes after its
        last renewal; a redelivery or the next start then takes over. A catalog without an S3
        location takes no lease.
- **Boundaries and data flow:** `control.py` (location, conditional put, list), `quarantine.py`
    (record, reader, command) and `lease.py` under `src/silver/` take an injected S3 client and
    clock. `main` composes them; `process_landing` gains the quarantined ids and a callable it
    calls before each write step.
- **Contracts and invariants:** a quarantine is all four tables of one run, in every mode. With
    equal records, incremental runs equal a backfill. A second writer writes nothing.
- **OOP / SOLID:** one frozen `Lease` (key, token, ETag, expiry) and two exceptions; functions
    otherwise. **Patterns:** none.
- **Deliberately rejected complexity:** quarantine by table or by row; processing commits one at
    a time so later ones pass; the operator's name in the record (personal data in the lake); a
    lock table in a database (a new resource); a lifecycle rule (its expiry counts days); a
    heartbeat thread or a signal handler; asking ECS whether the holder still runs (a new IAM
    right that never sees a laptop); a `--no-lease` flag.

## Approach

### Phase 1 — A refusal names its run

- [ ] **1.1 Checks name the run.** RED in `tests/test_silver_landing_v3.py`: of two pending runs
    one holds a null key column, a repeated key or a detail row without text; the message names
    that run id and not the other. Fails today: table and count only. GREEN: the three counts
    are grouped by `run_id`.
- [ ] **1.2 A commit is named by its key.** RED in `tests/test_silver_cli.py`: a commit file that
    is not JSON, and one whose `run_id` differs from its file name, exit with FEATURE-015's
    refusal code naming the key's run id. Fails today: a traceback, and a commit accepted under
    another id. GREEN: both listers hand the key's id to `parse_commit`.

### Phase 2 — Quarantine

- [ ] **2.1 Record and command.** RED in `tests/test_silver_quarantine.py`: a record is written
    under the versioned key with its four fields; a second write raises `FileExistsError` and
    leaves the first; against a fake S3 client it is one `put_object` with `IfNoneMatch="*"`;
    the reader returns ids and instants; the command exits 0, and with the refusal code for an
    existing record or an unknown run id. Fails: no module. GREEN: `control.py`, `quarantine.py`.
- [ ] **2.2 Pending without quarantined runs.** RED in `tests/test_silver_incremental.py`: one
    run of the synthetic landing gets a file that breaks its commit's count and is refused; with
    a record the batch passes, later runs are ingested, no table holds a row of that run id, a
    day it shares holds the other runs' rows, and a backfill gives the same tables. In
    `tests/test_silver_cli.py` a quarantined commit of garbage bytes is never read. Fails
    today: nothing subtracts a run. GREEN: listers skip by key; the set reaches `process_landing`.
- [ ] **2.3 The log shows it.** RED in `tests/test_silver_ingest.py` and the incremental module:
    the row as designed; `ingested_runs` omits it; a log that predates the column gains it with
    nulls; a run whose only news is a record writes the log alone and prints the id; a backfill
    writes it again; a deleted record makes the run pending and its row processed; a record for
    an ingested run raises `BackfillRequired` naming it and changes no snapshot, and a backfill
    then holds no row of it. Fails: no such column, and the last case returns.

### Phase 3 — Lease

- [ ] **3.1 The lease.** RED in `tests/test_silver_lease.py`, against a fake S3 client that
    honours both conditions: acquire on a free key; `WriterLeaseHeld` naming command and expiry
    while it is valid; takeover after expiry plus margin; of two takeovers of one ETag one wins;
    renewal extends, and raises `WriterLeaseLost` after a takeover; release deletes only its own
    object. Fails: no module.
- [ ] **3.2 The run holds it.** RED in `tests/test_silver_cli.py`: under a valid foreign lease
    `main` exits 1 with one line, reads no commit and publishes no `PendingRuns`; a run that
    succeeds, is refused or raises leaves no lease; a lease lost between two write steps stops
    the run with the later snapshots and the log unchanged; a local catalog takes none. Fails:
    `main` knows no lease.
- [ ] **3.3 Maintenance.** RED in FEATURE-020's test module: `rollback`, `pin` and
    `expire --apply` under a foreign lease exit 1 and change nothing. Fails: they take none.

### Phase 4 — Documents and operation

- [ ] **4.1 Role and documents.** `infra/silver/ecs.tf`: deny the task role `s3:PutObject` and
    `s3:DeleteObject` on `silver/_control/quarantine/*`, pinned by a test over the file's text as
    `tests/test_tooling_pins.py` pins the workflow. `docs/silver-rebuild.md`: quarantining and
    lifting in the dead-letter section, what a quarantine does to events and prices, the lease,
    deleting a stale one, the backfill's disabled mapping as advice. ADR 0003 decision 3 gets a
    dated amendment; `README.md:116-118` and `docs/architecture.html:1163` name the lease.
    Validation: `terraform fmt -check` and `validate`, leak guard, workflow validator.
- [ ] **4.2 Deployment and drill (operator; needs the owner's explicit approval; at most
    $0.10).** Read the real log's schema. Apply the role and the image. With the mapping
    disabled, start two tasks within seconds: one exits 0, one exits 1 with the lease line, no
    lease object remains, and the log has the new column. No real run is quarantined. Counts
    and exit codes go to `dev/plans/implementations/`.

## Files

- **Create:** `src/silver/control.py`, `src/silver/quarantine.py`, `src/silver/lease.py`.
- **Change:** `src/silver/landing.py`, `commits.py`, `ingest.py`, `rebuild.py`; FEATURE-020's
    maintenance module; `infra/silver/ecs.tf`; the four documents of task 4.1.
- **Tests:** `tests/test_silver_quarantine.py`, `tests/test_silver_lease.py` (new);
    `tests/test_silver_{landing_v3,cli,incremental,ingest}.py`; the Terraform text test.

## Test strategy

- **Unit:** the three checks per run; record, reader and command; every lease transition with an
    injected clock and a fake S3 client in `boto3`'s spelling that enforces both conditions.
- **Contract / fixtures, integration:** synthetic landing with one run damaged in the temporary
    tree, on a local SQLite catalog: refused, quarantined, processed, backfilled.
- **Retries / idempotency / evolution:** incremental equals backfill under equal records; a
    repeated run writes no second row; a log that predates the column; a lifted quarantine.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`, `ruff check` and
    `ruff format --check` on `src tests dev`, `python dev/tools/check_leaks.py`,
    `python dev/tools/validate_workflow.py`, `terraform -chdir=infra/silver validate`.
    **Manual:** the drill of 4.2.

## Operational and compliance impact

- **Data sourcing / legal:** landing is read as before and never written.
- **Privacy / security:** a record holds a run id and the operator's free text, which must name
    no listing; the lease holds a random token, no host name. One IAM deny, no new right.
- **Observability:** every run prints the number of quarantined runs; the log names them; a held
    lease is one line in the task log.
- **Failure and recovery:** a refused run waits for the operator: quarantine it, or fix Silver.
    A stale lease expires by itself and can be deleted by hand.
- **Migration / compatibility:** one optional column, added by the first run that writes the
    log. An older image run beside a newer one takes no lease: deploy, then drill.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| S3 requests | $0.005 per 1,000 PUT or LIST | 300 runs, 8 requests each | ~$0.01 |
| **Total (new AWS components)** | | | **~$0.01/month** |

- **Cost drivers & cheaper alternatives:** the lease's renewals. Cheaper is no code: a runbook
    page for the hand-made way out and the disabled mapping, at $0 and with today's risk.
    One-off: the drill, under $0.10.
- **External / non-AWS costs:** none. **Budget check:** yes.

## Success criteria

- [ ] Every landing refusal names the run id at fault.
- [ ] A quarantined run is read by no mode, the runs after it are ingested, and `landing_ingest`
    shows it with its instant; incremental equals backfill.
- [ ] A second writer, from the queue, `run-task` or a laptop, exits 1 before its first read, and
    a killed writer's lease is taken over after its expiry without a manual step.
- [ ] ADR 0003, runbook, README and architecture page describe this behaviour.
- [ ] Every behavior was implemented test-first; suites, lint, format, `terraform validate`, the
    leak guard and the workflow validator pass.
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced.

## Open questions & risks

- **Question:** who writes a quarantine record? Recommendation: the operator's command only. Its
    risk is delay: Silver stands until a person acts, with one mail per start. The alternative
    is the refused run writing it and continuing. Its risk is worse: a defect in a Silver check
    or a wrong image sets good runs aside without a person, in records that are written once.
- **Question:** may a quarantine be lifted by deleting the record by hand? Recommendation: yes,
    as a runbook step with the operator's own credentials; the alternative is a new run id only.
- **Question:** accept that queued starts during a long manual run fail three times and raise
    the dead-letter alarm unless the mapping is disabled? Recommendation: yes.
- **Risk:** quarantine becomes the routine answer and history gets holes. — *Mitigation:* each
    one needs a reason and shows in the log and in every run's report.
- **Risk:** the fake S3 client is kinder than S3. — *Mitigation:* the drill of 4.2.

## Progress log

- **2026-11-22** — Plan created from the architecture review of 2026-11-22 (B5, B7). Spikes on a
    SQLite catalog: PyIceberg 0.12.0 rebases a stale append; a column added to the log is
    metadata only.
