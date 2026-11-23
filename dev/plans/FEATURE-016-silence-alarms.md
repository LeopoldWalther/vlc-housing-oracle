# FEATURE-016 — Alarms for silence, a run-time limit and run telemetry

**Status:** 🔵 Planned · **Effort:** M (~10h) · **Priority:** High
**Branch root:** `feature/silence-alarms` · **Created:** 2026-11-22 · **Updated:** 2026-11-23

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-016.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-016-technical-plan.yaml`.

## Objective

A Silver pipeline that stops starting, hangs or costs more than expected sends mail, and every run
leaves one machine-readable line with its duration and peak memory. Today all of these are
silent, and the published cost figure describes a platform of six tables.

## Context

Source: the architecture review of 2026-11-22, items A1, B6, A6 and two bullets of section E. Each
claim below was checked against the code on that day.

- **Current state:**
    - `PendingRuns` is published only by a run, in the `finally` of `main`
        (`src/silver/rebuild.py:861-863`). The task always passes `--metrics`
        (`infra/silver/ecs.tf:197`), and a daily message at 05:30 UTC starts a run
        (`infra/silver/trigger.tf:63-67`) that publishes 0 when nothing is pending.
    - Its alarm needs six hourly datapoints above 0 and treats missing data as not breaching
        (`infra/silver/alerts.tf:117-130`). Runs take a minute and are hours apart, so six
        consecutive datapoints never exist: the alarm cannot fire.
    - The consumer raises 60 s before its 900 s timeout (`src/silver/consumer.py:19`, `:70-71`,
        `infra/silver/queue.tf:147`); the queue redelivers after 960 s, three times
        (`queue.tf:22`, `:25`). Nothing can stop a task: the consumer has no `StopTask` right
        (`queue.tf:85-106`) and the task has no deadline (`ecs.tf:171-218`).
    - A run reports through print lines and two custom metrics (`rebuild.py:875-896`,
        `:899-919`); the log is kept 30 days (`ecs.tf:46-49`). No budget exists in `infra/silver/`,
        and since 2026-11-23 none in the account: its one hand-made budget for all projects
        was deleted that day by the owner's decision, in favour of one per project. This stack
        tags its resources `project = "vlc-housing-oracle"` (`providers.tf:13-19`); the
        acquisition stack uses another tag key and value.
    - No test reads `infra/`; CI runs `terraform fmt -check` and `validate`
        (`.github/workflows/ci.yml:62-74`). `tests/test_tooling_pins.py` pins the workflow file
        by regular expressions over its text: the model for pinning Terraform here.
- **Problem:**
    - **A1, confirmed.** With the event source mapping left disabled after a backfill
        (`docs/silver-rebuild.md:717-747`), or a starter off, every alarm stays green; the mail
        within six hours that the FEATURE-004 plan promises (its lines 546-548) never comes.
    - **A1, corrected in one point.** The review expects the heartbeat to cover "commits stop
        arriving" too. It cannot: the daily run publishes 0 with or without commits. That silence
        belongs to collection, which the acquisition repository watches with its own alarm.
    - **B6, confirmed.** A run still healthy after the third invocation, 46 to 48 minutes, is
        dead-lettered under the text "Three Silver starts in a row failed" (`alerts.tf:104`). A
        hung task runs and bills without end: about $1.21 a day at the rate of the FEATURE-004
        plan, more than the platform costs in a month.
    - **A6 and telemetry, confirmed.** Nothing reports an unexpected bill. Duration and memory
        are recorded nowhere; the known durations, 30 s to 84 s, were read off log timestamps
        once (`dev/plans/implementations/FEATURE-008-listing-quality.md:30-33`).
    - **Cost statement, confirmed.** `docs/silver-rebuild.md:782-787`,
        `docs/architecture.html:1196-1197` and `README.md:21` carry FEATURE-004's estimate of
        nine runs a day over six tables, with one custom metric priced as free, the other at $0.30.
- **Constraints:** no new custom metric ($0.30 each a month) and no new dependency; every change
    in AWS is an operator step with the owner's approval; the repository is public.

## Scope

- **In scope:**
    - the `PendingRuns` alarm becomes a heartbeat; a new alarm on the age of the oldest message;
    - a run-time limit for queued runs that fits the consumer's waiting window, and truthful
        texts for the dead-letter alarm and the failure mail;
    - one cost budget for the platform, both repositories together, filtered by tag, with mail;
    - one JSON line per Silver run, a Logs Insights query in the runbook, log retention 90 days;
    - a cost statement per component in the runbook, with the two public pages agreeing.
- **Out of scope:**
    - bounded memory, less recomputation, a time window for property pairs (B2, B3, B9): they
        wait for four weeks of the numbers this feature produces;
    - a dashboard, a metric filter, an alarm on duration or memory, Container Insights;
    - the labeller's telemetry (FEATURE-017) and a way past a refused commit (FEATURE-023);
    - replacing the polling Lambda by a state machine; anything in the acquisition repository;
    - budgets of other projects in the same account, which their own repositories declare.
- **Users / consumers:** the operator, by mail and by one query; the later plan for B2 and B3.

## Dependencies

- **Needs:** none. FEATURE-015 edits the same `main` and the same exit-code legend: whichever
    lands second rebases, exit code 124 stays reserved for the limit, and the line's `outcome`
    takes FEATURE-015's names if they exist by then.
- **Unblocks:** the plan for B2, B3 and B9. FEATURE-019 and FEATURE-020 can cite the cost table.

## Open assumptions

- **Assumption:** an alarm may span two daily periods. — **Evidence:** unvalidated here; the
    acquisition repository runs an alarm over a longer range of daily periods. — **If wrong:**
    the apply of 4.2 refuses it; use one daily period and accept a rare false mail.
- **Assumption:** the first ten alarms and ten custom metrics of an account are free, a budget
    without a budget action costs nothing, and both stacks share one account. — **Evidence:**
    unvalidated; the price pages and the bill are read in 4.2 before the apply. — **If wrong:**
    up to $0.10 a month per alarm; the owner decides again on the budget.
- **Assumption:** a budget's tag filter accepts the two stacks' different tag keys and counts a
    cost that carries either, and the pinned AWS provider can activate a cost-allocation tag. —
    **Evidence:** unvalidated; `terraform validate` in 3.3 answers the second, the budget's own
    figures after 4.2 the first. — **If wrong:** both stacks add one shared tag to their default
    tags (a coordinated change on the acquisition side) and the filter names that one.
- **Assumption:** tagged resources carry nearly all of the platform's cost. — **Evidence:**
    unvalidated. A cost-allocation tag counts from its activation on, shows after about a day,
    and never covers what cannot be tagged (some data transfer and request charges). — **If
    wrong:** the project budget under-reports, and since the account-wide budget is gone nothing
    else mails for untagged cost; the success criterion compares the budget's figure with the
    bill after one full month.
- **Assumption:** `ApproximateAgeOfOldestMessage` is reported while a message waits behind a
    disabled mapping. — **Evidence:** unvalidated; the drill of 4.3 shows it. — **If wrong:**
    the heartbeat still fires within two to three days.
- **Assumption:** `ru_maxrss` includes DuckDB's memory, because DuckDB runs in the process. —
    **Evidence:** standard library documentation. — **If wrong:** nothing breaks; the kernel
    counts page cache against the task's 2,048 MiB too, so the figure is a lower bound anyway.

## Design

- **Primary approach:** five small changes, each built on what exists.
    - **Heartbeat.** The alarm `pending_runs` keeps its name and metric and becomes: `Minimum`
        over 86,400 s, two of two periods above 0, missing data `breaching`. It fires when for
        two days no run ended with nothing pending, whatever the cause. A healthy day holds a
        zero from the 05:30 run, so no period alignment gives a false alarm. Detection: two
        to three days.
    - **Queue age.** A new alarm on `AWS/SQS` `ApproximateAgeOfOldestMessage` of the main queue:
        `Maximum` over 300 s above 21,600 s, missing data not breaching. It restores the six hours
        FEATURE-004 promised for a mapping left disabled.
    - **Run-time limit.** `--run-time-limit SECONDS` arms a daemon timer that writes all stack
        traces to standard error and ends the process with exit code 124. The task definition
        passes `run_time_limit_seconds`, default 1,800. A manual backfill overrides the whole
        command and so runs without a limit. The limit plus 300 s for provisioning must fit
        `maxReceiveCount` × (Lambda timeout − 60 s) = 2,520 s, the window if a failed batch
        returns at once; a test pins the inequality. The consumer's code does not change.
    - **Budget.** One monthly cost budget for the platform in `infra/silver/budget.tf`, amount
        `monthly_budget_usd`, mail to `alert_email` at 80 % actual and 100 % forecast. Its
        filter names this stack's project tag and the acquisition stack's, passed as the
        variable `budget_tag_filters` so the other stack's tag is not written here. The same
        file activates both tags as cost-allocation tags. The budget is declared here once for
        both repositories; the acquisition repository declares none.
    - **Telemetry.** `main` prints one last line in every ending it reaches, with the keys
        `event` (`silver_run`), `mode`, `outcome` (`ok`, `nothing_pending`, `refused`, `failed`),
        `pending_runs`, `processed_runs`, `affected_listings`, `duration_seconds`, `peak_rss_mib`
        and `transformation_version`. A run killed by the kernel or the limit prints none; its
        exit code is in the failure mail.
- **Boundaries and data flow:** `src/silver/telemetry.py` holds two pure functions: the unit of
    `ru_maxrss` (kibibytes on Linux, bytes on macOS) and the line. `main` measures and prints;
    the clock is a parameter, as in `src/enrichment/evaluate.py:112`.
- **Contracts and invariants:** no table, schema or rule changes: **no new
    `transformation_version` and no backfill.** The existing print lines stay. Ending a run at
    the limit is a crash like exit 137: the ingest log is written last, so the next run repeats
    the same pending set.
- **OOP / SOLID, patterns:** pure functions and two injected callables (clock, timer); none.
- **Deliberately rejected complexity:**
    - raising `maxReceiveCount`, as the review suggests: it also multiplies the retries and
        mails of a run that fails for a real reason;
    - `timeout` as the container's entry point: a task override cannot replace an entry point,
        so the operator's backfill would be cut off too;
    - `StopTask` from the consumer: a new IAM right that acts only when a message is redelivered;
    - a heartbeat metric, a metric filter on the line, a dashboard: each is a priced object;
        duration and memory as columns of `landing_ingest`: a schema change for a log's job.

## Approach

### Phase 1 — One telemetry line per run

- [ ] **1.1 The line and the memory unit.** RED: new `tests/test_silver_telemetry.py` asserts
    that `peak_rss_mib(2_097_152, "linux")` and `peak_rss_mib(2_147_483_648, "darwin")` are both
    2048.0 and that `run_line(...)` is one line of JSON with exactly the nine keys; it fails on
    import, the module does not exist. GREEN: `src/silver/telemetry.py`.
- [ ] **1.2 Every ending prints it.** RED: in `tests/test_silver_cli.py`, a run with a fake clock
    returning 100.0 and then 142.5 ends its output with a line that parses as JSON with
    `duration_seconds` 42.5, `outcome` `ok`, a positive `peak_rss_mib` and the run counts; a
    second run gives `nothing_pending`; a landing with a missing file gives `refused` and exit
    1. All fail today: the last line is not JSON. GREEN: `main` takes `clock`, sets the outcome
    as it goes and prints in its `finally`. REFACTOR: `_report` stays as it is.
- [ ] **1.3 Query and retention.** RED: new `tests/test_infra_silver.py` cuts a resource block
    out of the Terraform text and asserts that the task's log group keeps 90 days; fails at 30.
    GREEN: `ecs.tf`. The runbook gains the query (`filter event = "silver_run"`, newest first).

### Phase 2 — A run-time limit inside the consumer's window

- [ ] **2.1 The limit.** RED: `tests/test_silver_cli.py` asserts that
    `arm_run_time_limit(1800, timer=FakeTimer, exit=fake_exit)` starts a daemon timer of 1,800 s
    whose callback writes `run-time limit of 1800 s reached` to standard error and calls the
    exit with 124; that `main` arms it only when given `--run-time-limit` and cancels it on
    return; and that 0 or a negative value is a usage error (exit 2). Fails today: the flag is
    unknown. GREEN: the function, the argument, the call.
- [ ] **2.2 The task passes it, and the window is pinned.** RED: `tests/test_infra_silver.py`
    reads the Lambda timeout and `maxReceiveCount` from `queue.tf`, `DEADLINE_MARGIN_MS` from the
    consumer and the default of `run_time_limit_seconds` from `variables.tf`, and asserts
    default + 300 ≤ receives × (timeout − margin) and that the task's command names
    `--run-time-limit`; fails, the variable does not exist. GREEN: the variable and `ecs.tf`.
- [ ] **2.3 Truthful texts.** RED: the same file asserts that the dead-letter alarm's description
    names the run-time limit and that the failure mail's legend names 124; both fail. GREEN:
    `alerts.tf`: the description lists the three causes (three failed starts; one run ended at
    its limit; a backfill adopted with the mapping enabled that outlived the window).

### Phase 3 — Alarms and the budget

- [ ] **3.1 Heartbeat.** RED: the `pending_runs` block has `period = 86400`, two evaluation
    periods, two datapoints and `treat_missing_data = "breaching"`; fails on all four. GREEN:
    the block, and a description that names both readings (nothing starts; runs stay pending).
- [ ] **3.2 Queue age.** RED: a block `queue_age` exists with the metric name, the main queue's
    name as dimension, threshold 21600 and `notBreaching`; fails, no such block. GREEN: the alarm.
- [ ] **3.3 Budget.** RED: `budget.tf` holds one `aws_budgets_budget` of type `COST`, monthly,
    with a tag filter from `var.budget_tag_filters` and two notifications to `var.alert_email`,
    and one cost-allocation tag resource per filtered key; fails, no such file. GREEN: the
    resources and the two variables. The phase ends with `terraform fmt -check` and `validate`
    green.

### Phase 4 — Documentation and operation

- [ ] **4.1 Runbook and public pages.** The command section (flag, line, exit 124), the settings
    table, the alarm table, the dead-letter section, the backfill section (no limit; the alarms
    tolerate six hours and two days of a disabled mapping) and "Turning it off" (the heartbeat
    mails once). `docs/architecture.html` lines 1174 and 1219 say what is now true. Validation:
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`, the test suite.
- [ ] **4.2 Deployment (operator, needs the owner's approval).** Read the price pages for
    alarms and budgets. Set `silver_image` to the merged commit and plan: expect new revisions of
    both
    task definitions (the labeller shares the image) with the references to them, two changed
    alarms, the failure mail's text, the log group, one new alarm, one budget and two activated
    tags, nothing else.
    Apply. After the next 05:30 run the line is in the log, the query returns it and the
    heartbeat is `OK`.
    **Upper bound: $0.10 a month recurring, $0.05 once.**
- [ ] **4.3 Silence drill (operator, needs approval).** After a week's walk has ended, disable
    the event source mapping until both mails have arrived, at most 72 hours: the queue-age mail
    six hours after the next 05:30 message, the heartbeat mail within three days. Enable the
    mapping; both clear and Silver catches up. Record the times. **Upper bound: $0.01.**
- [ ] **4.4 Cost statement (after one full weekly walk on the new image).** Replace
    `docs/silver-rebuild.md:782-787` by a table per component: Fargate from the durations and
    run counts of the telemetry, the public address, both custom metrics and all alarms under
    one stated rule, logs, S3 storage with the note that snapshots are kept without expiry until
    FEATURE-020, the labeller and the model API as they stand. One sentence says that collection
    is billed outside AWS and documented in the acquisition repository. The owner reads the
    month's bill per service in the Billing console (free; no Cost Explorer API call).
    `docs/architecture.html:1196-1197` and `README.md:21` take the new total and its date.

## Files

- **Create:** `src/silver/telemetry.py`; `infra/silver/budget.tf`;
    `tests/test_silver_telemetry.py`; `tests/test_infra_silver.py`.
- **Change:** `src/silver/rebuild.py` (clock, outcome, line, limit); `infra/silver/alerts.tf`
    (heartbeat, queue age, two texts); `infra/silver/ecs.tf` (two arguments, retention);
    `infra/silver/variables.tf` (two variables); `docs/silver-rebuild.md`;
    `docs/architecture.html`; `README.md`.
- **Tests:** `tests/test_silver_cli.py` gains the endings and the limit.

## Test strategy

- **Unit:** the unit conversion on both platforms, the line's keys, the four outcomes, the
    timer's callback with a fake exit, the usage errors.
- **Contract / fixtures, integration:** `main` in process on the synthetic landing and the
    SQLite catalog of the existing CLI tests; no real data.
- **Retries / idempotency / evolution:** a run ended at the limit is an interrupted run, which
    `tests/test_silver_incremental.py:206` already shows to be repeated to the same result.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`, `ruff check` and
    `ruff format --check` on `src tests dev`, `terraform fmt -check` and `validate` under
    `infra/silver/`, the leak guard, the workflow validator.
- **Manual:** the plan output of 4.2 and the drill of 4.3.

## Operational and compliance impact

- **Data sourcing / legal:** N/A. **Observability:** this feature is the change; see Design.
- **Privacy / security:** the line holds counts, a version and two measurements, no identifier of
    a listing. No new IAM right. The budget mails the address the alarms already use.
- **Failure and recovery:** a run ended at its limit sends the failure mail with exit 124, then
    the dead-letter alarm; the fix is a larger task, a higher limit together with
    `maxReceiveCount`, or a backfill by hand. The heartbeat also mails, once, when the operator
    turns the starters off on purpose.
- **Migration / compatibility:** the alarm keeps its name and changes in place. An older image
    does not know `--run-time-limit`: image and task definition are deployed in one apply.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Heartbeat alarm | $0.10 per alarm beyond the free ten | replaces an alarm, count unchanged | $0.00 |
| Queue-age alarm | same | fourth alarm of this stack, inside the free ten | $0.00, else $0.10 |
| Project budget with two mails, two cost-allocation tags | free without a budget action | one budget, no action | $0.00 |
| Custom metrics | $0.30 each | none added | $0.00 |
| Logs (one line per run, 90 days) and Logs Insights | about $0.60 per GB ingested, $0.006 per GB scanned | megabytes a month | $0.00 |
| **Total (new AWS components)** | | | **~$0.00/month, at most $0.10** |

- **Cost drivers & cheaper alternatives:** the only priced object is the queue-age alarm, if the
    account is past ten alarms. Cheaper: leave it out and rely on the heartbeat, which finds a
    disabled mapping in two to three days instead of six hours. The limit lowers the worst case:
    a hung run costs about $0.03 once instead of $1.21 a day.
- **External / non-AWS costs:** none. **Budget check:** yes.

## Success criteria

- [ ] With the mapping disabled, a mail arrives after six hours and another within three days.
- [ ] A queued run cannot outlast `run_time_limit_seconds`; its mail and the dead-letter alarm's
    text say what happened.
- [ ] Every run that ends in Python leaves one JSON line; one query lists duration and memory.
- [ ] The project budget mails at 80 % actual and 100 % forecast, and after one full month its
    actual figure is within 10 % of the bill's lines for both stacks.
- [ ] The runbook, the architecture page and the README state one cost total with its date.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured type, lint, format, infrastructure, and workflow checks pass
- [ ] Data, operational, migration, and cost contracts are documented where affected
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner, before 2.1):** end a queued run at a limit, or only document the 46-minute
    window and correct the text? Recommendation: end it, at 1,800 s, twenty times the longest run
    measured; a hung task otherwise costs more per day than the platform per month.
- **Question (owner, before 3.3):** the amount. The account stood at $8.33 on 2026-11-23
    against the $5 limit of the budget deleted that day, cause unknown. Recommendation: read the bill by service
    first, then set this budget to about twice the platform's real monthly cost.
- **Risk:** the account has no cost alarm from 2026-11-23 until task 4.2 is applied. —
    *Mitigation:* none in this plan; a budget made by hand in the meantime is deleted in 4.2.
- **Question (owner, before 4.2 and 4.3):** approve the apply and the drill, which holds Silver
    back for up to three days in an idle part of the week. Recommendation: both; the drill is
    the only proof that silence mails.
- **Risk:** a healthy run grows past the limit and is ended. *Mitigation:* the mail names exit
    124 and the variable; the query shows the trend weeks earlier; the backfill has no limit.
- **Risk:** the timer cannot fire if native code hangs while holding the interpreter lock.
    *Mitigation:* DuckDB and the S3 clients release it; the account budget is the last line of
    defence.

## Progress log

- **2026-11-22** — Plan written from the architecture review of 2026-11-22 (A1, B6, A6, section E
    bullets three and six). Findings checked against the code; A1 corrected in one point.
- **2026-11-23** — A read of the account showed a hand-made account-wide budget. The owner
    decided on budgets per project: this plan declares the platform's, for both repositories,
    filtered by tag (task 3.3). The account-wide budget was deleted the same day.
