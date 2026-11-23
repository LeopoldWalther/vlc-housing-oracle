# FEATURE-017 — Labeller run safety: a served version, one run at a time, stable answers

**Status:** 🔵 Planned · **Effort:** M (~10h) · **Priority:** High
**Branch root:** `feature/labeller-run-safety` · **Created:** 2026-11-22 · **Updated:** 2026-11-22

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-017.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-017-technical-plan.yaml`.

## Objective

Before the text labeller is enabled in AWS, close the ways in which it can empty the label table,
pay for a text twice, change a stored label or send text of a superseded redaction: Silver serves
a label version of its own, at most one labeller runs, and an answer is written once and read
the same way ever after.

## Context

- **Current state:** FEATURE-012 built the labeller (`src/enrichment/`), its task, roles and start
    rule (`infra/silver/labels.tf`) and the Silver table `listing_text_label`
    ([ADR 0005](../../docs/adr/0005-model-derived-labels.md)). The code is merged; the first batch
    in AWS, the backlog and `labeller_enabled = true` are FEATURE-012 task 4.3 and not done.
- **Problem:** the architecture review of 2026-11-22 named these weaknesses. Each was checked
    against the code and is true:
    1. **One constant is written and served.** `LABEL_VERSION` (`src/enrichment/prompt.py:9`) is
        what the labeller writes and what Silver joins (`src/silver/rebuild.py:708`,
        `src/silver/sql/listing_text_label.sql:8`). The table is replaced whole, so the first
        Silver run after a change writes null labels for every listing. They return at 2,000
        texts per labeller run (`infra/silver/variables.tf:88-97`): eight runs for 15,600 texts.
    2. **Labeller runs overlap.** A rule starts one task per succeeded Silver task
        (`infra/silver/labels.tf:148-164`), outside the queue that serializes Silver. A batch is
        open only once its record exists, which is written after the submit
        (`src/enrichment/text_labels.py:223-235`), so two overlapping runs submit the same
        texts. Never observed: the labeller has not run in AWS.
    3. **Write-once is a check before the write** (`src/enrichment/text_labels.py:320-326`), and
        the role may overwrite (`infra/silver/labels.tf:60-64`). Two runs that collect one batch
        end in an uncaught `FileExistsError` and a failure mail. Silver takes the newest answer
        (`src/silver/sql/listing_text_label.sql:9`), so a second answer changes a served label.
    4. **An invalid answer is counted and dropped** (`src/enrichment/text_labels.py:149`, `:161`),
        so its text is pending again after every Silver run and `pending` never reaches 0, the
        signal item 1 needs. Rare (0 of 400 answers in the evaluation), but unbounded.
    5. **Every version is read, part by part** (`src/enrichment/text_labels.py:280-291`), by both
        commands (`src/silver/rebuild.py:247`); a part of a superseded version that lacks a
        column stops them. The cost is seconds, not dollars: one small part per batch.
    6. **The labeller ignores `redaction_version`** (`src/enrichment/text_labels.py:416-421`),
        although `listing_text` carries the column (`src/silver/sql/listing_text.sql:13`).
- **What the code settles beyond the review:** the repository has no helper for a conditional
    write; `If-None-Match` appears only as a runbook command (`docs/silver-rebuild.md:628-633`)
    and in ADR 0003. The pinned `boto3` models `IfNoneMatch` on `PutObject` (checked in the
    installed package), so no dependency is added.
- **Constraints:** every model call in a test is faked. An AWS change and a paid run are
    operator steps with the owner's approval. The stored layout and both schemas keep their shape.

## Scope

- **In scope:** a served label version; reading one version; refusing text of another redaction
    version; storing invalid answers; the first valid answer wins; conditional writes of parts
    and records, enforced by the role; the labeller started by the queue consumer; the documents.
- **Out of scope:** compacting parts or reading them with one statement (wait for the run
    durations of FEATURE-016); a write-ahead record before the submit; a guard against a mistyped
    `--text-labels` location; the dead-letter text (FEATURE-016); the Silver half of the version
    guard (FEATURE-015); the legal basis (FEATURE-024); how the Lambda is deployed (FEATURE-019).
- **Users / consumers:** readers of `listing_text_label`; the operator of a new label version.

## Dependencies

- **Needs:** FEATURE-012 phases 1 to 4.2 — merged. Nothing of FEATURE-015 or FEATURE-016.
- **Unblocks:** FEATURE-012 task 4.3 (the first batch and the backlog run on this code);
    FEATURE-024 (a redaction r3 cannot send r2 text). FEATURE-022 changes the same modules later.

## Open assumptions

- **Assumption:** IAM evaluates the condition key `s3:if-none-match` on `s3:PutObject`, so a role
    can be allowed conditional writes only. — **Evidence:** AWS's documentation as the Architect
    recalls it; no AWS call was made. — **If wrong:** task 4.3's first record is denied although
    the header is sent; the condition is removed and the code's conditional write remains.
- **Assumption:** the answer store holds no two answers for one text. — **Evidence:** the
    labeller is off by default and task 4.3 is open. — **If wrong:** task 4.2 counts the
    objects; a duplicate means one label may change once, to the first answer.

## Design

- **Primary approach:**
    - **Served version.** `SERVED_LABEL_VERSION` beside `LABEL_VERSION` in
        `src/enrichment/prompt.py`, both `tl3` today. Silver joins the served one; the labeller
        writes, collects and counts under the written one. A new version is two commits: the
        written one first, the served one once a labeller run prints `pending: 0` and
        `batches open: 0`. A constant, not an argument: one revision serves one version.
    - **One version read.** `stored_answers` takes the version and lists its directory only.
    - **Redaction.** `check_redaction` refuses a run, like `check_model`, when a row of
        `listing_text` carries a `redaction_version` other than the code's: exit 1, "backfill
        Silver first". Ended batches are still collected; nothing is submitted. The version
        constants move to `src/silver/versions.py`: `rebuild.py` imports the labeller's reader.
    - **Invalid answers.** A succeeded request without a valid label is stored as a row with null
        labels and its tokens; the schema allows it. Its text is no longer pending.
    - **The first valid answer wins.** Silver's rule drops rows without labels and takes the
        oldest, ordered by `labeled_at`, `renovation_state`, `is_auction`: a total order.
    - **Conditional writes.** A part and a record are written with one `PutObject` carrying
        `IfNoneMatch="*"`, a local file with exclusive creation. An existing part means another
        run collected the batch and is skipped; an existing record is an error. The role may
        `s3:PutObject` only with the header, so the storage refuses any other write.
    - **Serialized start.** After a Silver task that exits 0 the consumer lists running tasks of
        the labeller family and starts one unless one runs. Consumer invocations never overlap
        (`src/silver/consumer.py:3-5`), so list-then-start has no race. It does not wait. A
        failed start raises and is redelivered. The rule, its target and its role are removed.
- **Contracts and invariants:**
    - Layout and schemas of parts and records are unchanged; a part row with null labels means
        "answered, not valid". `listing_text_label` keeps its columns; null gains that meaning.
    - **No new `transformation_version` and no backfill.** The table is replaced whole by every
        run, and for every answer stored today the old and the new rule write the same rows:
        they differ only for duplicates and invalid answers, which the store does not hold.
    - `labeller_enabled = false` leaves the consumer without a labeller definition: none starts.
    - A Silver task started by hand no longer starts the labeller; the next queued run does, at
        the latest the daily catch-up (`infra/silver/trigger.tf:63-81`).
- **OOP / SOLID:** pure functions; the S3 client is injected into the two writers, as
    `src/silver/rebuild.py:821-823` injects it into the commit reader.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - the consumer waiting for the labeller: an empty API credit would dead-letter Silver's queue;
    - a lease object in S3: a crashed task leaves it, and telling stale from held needs a clock;
    - a second queue and consumer for the labeller: four resources for one list call;
    - falling back to the previous version's answer per row: a table of mixed versions;
    - retrying an invalid answer n times: a counting rule for an event measured at 0 of 400.

## Approach

### Phase 1 — What is served and what is sent

- [ ] **1.1 Served version.** RED (`tests/test_silver_listing_text_label.py`): with
    `SERVED_LABEL_VERSION` patched to differ from `LABEL_VERSION`, a text with an answer of each
    carries the served one. Fails: the name does not exist. GREEN: the constant, and
    `listing_text_labels` sets the variable from it.
- [ ] **1.2 One version read.** RED (`tests/test_enrichment_text_labels.py`): `stored_answers`
    with a version returns that version's rows, and a part of another version that lacks a
    column raises nothing. Fails: the function takes two arguments. GREEN: the third argument;
    the labeller passes the written version, Silver the served one.
- [ ] **1.3 Redaction refusal.** RED: `check_redaction` raises `RedactionVersionError` for a row
    of another version and names versions only; the command, with the code's version patched,
    exits 1, collects an ended batch and submits nothing. Fails: the names do not exist and the
    command exits 0. GREEN: `src/silver/versions.py`, the check, `redaction_version` in the read.

### Phase 2 — Answers that never change

- [ ] **2.1 The first valid answer wins.** RED (`tests/test_silver_listing_text_label.py`): of two
    answers the older wins; two with one `labeled_at` give the same row in either input order;
    an answer without labels gives five nulls and a later valid one is taken. Fails: the rule
    takes the newest; `test_the_newer_of_two_answers_wins` is rewritten. GREEN: filter and order.
- [ ] **2.2 Invalid answers are stored.** RED (`tests/test_enrichment_text_labels.py`): a batch
    of two valid answers and one invalid gives a part of three rows, the third with null labels
    and its tokens; the next command run does not submit that text. Fails: the part has two rows
    and the text is pending. GREEN: `_answer_part` keeps every succeeded request.
- [ ] **2.3 Conditional writes.** RED: against a fake S3 client the writer sends one `put_object`
    with `IfNoneMatch="*"` and maps `PreconditionFailed` to `FileExistsError`; a local second
    write raises it too; `collect_batches` skips a batch whose part exists; a record that exists
    fails the run. Fails: no client is taken, a second collection raises. GREEN: both branches.

### Phase 3 — One labeller at a time

- [ ] **3.1 The consumer starts the labeller.** RED (`tests/test_silver_consumer.py`): after exit
    0 and with no labeller running, one labeller task is started with the network configuration;
    with one running, after a non-zero exit or without the definition in the environment, none;
    a failed start raises `TaskFailed`. Fails: the handler starts one task only. GREEN: the step.
- [ ] **3.2 Infrastructure.** Validation first: a test that reads `infra/silver/*.tf` as text, as
    `tests/test_tooling_pins.py` reads the workflow, and expects no labeller event rule, the
    consumer's labeller variables and `s3:if-none-match` on the labeller's `PutObject`. Then:
    remove the rule, its target and start role; give the consumer the two variables, `RunTask`
    and `PassRole`; split the labeller's S3 statement. `terraform fmt -check` and `validate`.

### Phase 4 — Operation

- [ ] **4.1 Documentation.** An amendment to ADR 0005; the runbook's flow, layout, command, exit
    codes, "A new label version" (the two commits and their signal), settings and diagram; the
    failure mail's exit-1 causes; `README.md` and `docs/architecture.html` say who starts the
    labeller; a progress line in FEATURE-012. Validation: leak guard and workflow validator.
- [ ] **4.2 Apply (operator, with the owner's explicit approval; $0).** Count the objects under
    the answers' prefix (expected: none). `terraform plan` must show the rule, target and start
    role destroyed, the consumer and two role policies changed; apply, the labeller still off.
- [ ] **4.3 First batch of one text (operator, with the owner's explicit approval; paid, at most
    $0.05).** This is the first batch of FEATURE-012 task 4.3, run by hand with `--max-texts 1`:
    the record exists, a run after the batch's end writes the part, a third run writes nothing
    and exits 0. Counts only, recorded in `dev/plans/implementations/`.

## Files

- **Create:** `src/silver/versions.py`; `tests/test_infra_labeller.py` (task 3.2).
- **Change:** `src/enrichment/prompt.py`, `src/enrichment/text_labels.py`,
    `src/silver/rebuild.py`, `src/silver/sql/listing_text_label.sql`, `src/silver/consumer.py`;
    `infra/silver/labels.tf`, `queue.tf`, `variables.tf` (a description), `alerts.tf` (one
    sentence of the mail); `docs/adr/0005-model-derived-labels.md`, `docs/silver-rebuild.md`,
    `README.md`, `docs/architecture.html`, `dev/plans/FEATURE-012-text-labels.md` (progress log).
- **Tests:** the three existing test modules named in the tasks.

## Test strategy

- **Unit:** every behaviour of phases 1 to 3, on synthetic rows and faked batches.
- **Contract / fixtures:** synthetic landing only; a fake S3 client with `boto3`'s spelling, as
    in `tests/test_silver_cli.py`; the two schema tests stay unchanged.
- **Integration:** the existing command tests on a local catalog: labeller, then Silver.
- **Retries / idempotency / evolution:** collecting a batch twice; a part of an older version
    with another shape; stepwise commits equal a backfill over the same answers.
- **Configured quality checks:** `python -m unittest` for the four test modules, then the suite;
    `ruff check`, `ruff format --check`; Terraform as in 3.2; leak guard; workflow validator.
- **Manual:** tasks 4.2 and 4.3.

## Operational and compliance impact

- **Data sourcing / legal:** nothing new crosses the landing contract or leaves the account.
- **Privacy / security:** text of a superseded redaction is never sent. The labeller's role
    loses the right to overwrite; the consumer may start one more task definition. Errors and
    logs name versions and counts, never a text, a hash or a listing.
- **Observability:** the report's lines are unchanged; `pending: 0` becomes reachable.
- **Failure and recovery:** a failed start is retried by the queue, then dead-lettered.
- **Migration / compatibility:** no schema change, no backfill, no new `transformation_version`.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| EventBridge rule and target | free for AWS service events | removed | $0.00 |
| Lambda consumer | per request and duration | two more API calls per invocation | $0.00 |
| S3 | $0.005 per 1,000 PUTs | about 60 conditional PUTs, as many as today | $0.00 |
| ECS Fargate (labeller) | ARM64, 0.25 vCPU | at most as many tasks as today | ~$0.10, unchanged |
| **Total (changed AWS components)** | | | **~$0.00/month more** |

- **Cost drivers & cheaper alternatives:** none in AWS; three resources go, none is added.
    Cheaper still: change nothing and accept one capped batch paid twice, $0.22 per collision.
- **External / non-AWS costs:** the Claude API, from the plan's credit, as before: about $0.35 a
    month. Task 4.3 sends one text, under $0.01. A new label version costs the backlog, $1.70.
- **Budget check:** yes; the analytics side stays under $1 a month.

## Success criteria

- [ ] A changed `LABEL_VERSION` alone changes no row of `listing_text_label`.
- [ ] Two answers for one text and version give the same served label in every run.
- [ ] A text with an invalid answer is submitted once per label version.
- [ ] A second write of a part or record is refused by the code and, in AWS, by the role.
- [ ] No labeller task starts while another runs, and none when `labeller_enabled` is false.
- [ ] A run with a row of another redaction version submits nothing and exits 1.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured type, lint, format, infrastructure, and workflow checks pass
- [ ] Data, operational, migration, and cost contracts are documented where affected
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner, at review):** is the served version a constant changed by a commit, or a
    setting of the Silver task? Recommendation: the constant, for a reproducible backfill.
- **Question (owner, at review):** is one invalid answer final for its label version?
    Recommendation: yes; a retry rule can follow if real runs show more than a handful.
- **Question (owner, at review):** the review asks to send only texts of the current redaction;
    this plan refuses the whole run. Recommendation: refuse; a skipped row would be silent.
- **Question (owner, at review):** a new `transformation_version`? Recommendation: none, as
    argued under the contracts.
- **Risk:** a crash between submit and record still leaves a batch nobody collects and pays its
    texts twice, about $0.22 at the cap; FEATURE-012 accepted it. — *Mitigation:* the run
    exits 1 and mails; task 4.3's one text proves the write path before a capped batch.
- **Risk:** an operator starts a second labeller by hand. — *Mitigation:* the runbook says to
    list running tasks first; the writes and Silver's rule keep the labels stable.

## Progress log

- **2026-11-22** — Plan written from the architecture review (section C, bullets 1 to 3, and the
    labeller half of A2). Every finding is true in the code; three fixes differ from its proposal.
