# Review — FEATURE-012: Labels read from the description by a language model

**Reviewer:** `reviewer` · **Date:** 2026-11-16 · **Plan:** [FEATURE-012](../plans/FEATURE-012-text-labels.md)
**Verdict:** ✅ Approved

## Summary

The design is sound: the model's answers are immutable inputs keyed by a hash of the text, the
labeller finds its work by a set difference, and Silver stays a pure function of its inputs. The
evaluation comes before anything is built on the model, which is the right order of risk. Five
corrections were needed, and all follow from the repository or from counts on Silver:

- the paid evaluation needs the prompt, the Bedrock call and the evaluation command, which the
    plan built only in phase 2;
- the title is not redacted, so it must not leave Silver;
- a sample enriched with keyword hits overstates precision, and for a label as rare as an
    auction 200 texts cannot prove it;
- the labels had values and no definitions, so a person and a prompt could not agree on them;
- the start rule as written would let the labeller start itself.

They are folded into the plan. The user answered the open question: the user labels the
evaluation set from a file sent by mail.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-012-text-labels.md` (🔵 Planned, registered).
    FEATURE-006 is 🟢 Complete. FEATURE-009 is implemented and in review as a pull request;
    FEATURE-010 and FEATURE-011 are approved and not implemented. Phases 1 and 2 touch no file
    those features change; phase 3 changes `src/silver/rebuild.py` after them.
- **Code, tests, data, and configuration:**
    - `src/silver/sql/listing_text.sql`: only `description` passes through `redact_personal`;
        the statement's comment says titles, location and features stay verbatim.
    - `src/synthetic/landing.py`: every listing is a sale (`_area` defaults to `sale` and no
        scenario overrides it), and most detail records share one description. A rent listing
        therefore needs hand-built rows in a unit test; identical texts are covered for free.
    - `Dockerfile`: the entry point is `python -m src.silver.rebuild`, and `src/` is copied
        whole.
    - `requirements.txt`: `boto3` is not pinned; it arrives through `pyiceberg[glue]`, as
        1.43.114 in the development environment.
    - `infra/silver/alerts.tf`: the failure rule matches every stopped task of the cluster and
        its mail text speaks of a Silver task and the queue's retries.
    - `infra/silver/ecs.tf`: the Silver task's command is a list in the task definition, so
        `--text-labels` is one more element.
- **Evidence reviewed:** Athena, counts only, Silver of 2026-11-16, sale listings joined to
    their text row (17,236):

    | Stratum | Listings |
    | --- | --- |
    | A: the description contains the auction keyword | 153 |
    | B: `good`, a needs-renovation keyword | 335 |
    | C: `needs_renovation`, a needs-renovation keyword | 880 |
    | D: `good`, a renovated keyword only | 3,761 |
    | E: `needs_renovation`, a renovated keyword only | 180 |
    | F: `good`, no keyword | 9,599 |
    | G: `needs_renovation`, no keyword | 975 |
    | H: `new`, no auction keyword | 1,225 |
    | not sampled: empty description (86), no `condition` (42) | 128 |

    The strata are disjoint in this order and sum to 17,236 with the last row.
- **Unverified assumptions:**
    - The model reads the two meanings well enough. Owner: operator and user. Verification
        point: task 1.6, before any pipeline code is written.
    - Bedrock's price and the tokens per text. Owner: operator. Verification point: task 1.6.
    - A sequential run is fast enough for the backlog. Owner: operator. Verification point:
        task 1.6 records seconds per text; at two seconds the backlog takes about ten hours in
        one task, which is acceptable for a one-time run.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | After M3 and M4 |
| Repository and dependency assumptions match reality | Pass | After M1 and M5 |
| Supporting evidence is reproducible and sufficient | Pass | Stratum counts above; the model's quality is gated by task 1.6 |
| Design is the simplest adequate option | Pass | One call per text; no agent, no batch, no second writer |
| TDD slices and validation commands are executable | Pass | After M1; no test calls a model |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | After M2 and M6 |
| Operations, recovery, migration, and cost are addressed | Pass | After M5; about $1.10 a month |

## Strengths

- The answers are inputs, not computations: a Silver backfill costs nothing and gives the same
    table.
- Pending work is a set difference over hashes, so a crashed run resumes without a state store.
- The model is behind one narrow protocol, the only place in the repository where an interface
    is justified by a paid external boundary.
- The expensive first design was replaced before review, and the cheaper alternative (keyword
    rules) is named.

## Findings

### 🟡 M1 — The evaluation needs code that phase 2 built

- **Problem:** task 1.3 ran the model on the evaluation set, while the prompt, the request and
    the Bedrock labeller were tasks 2.2 and 2.3.
- **Evidence:** the plan's phase order; `src/enrichment/` does not exist.
- **Impact:** the Implementer would have to invent the model call inside the evaluation task.
- **Recommendation:** phase 1 builds, in order, the answer type and its validation with the
    prompt, the Bedrock labeller against a stubbed client, and the evaluation command with a fake
    labeller; the paid run is its own operator task and gates phases 2 to 4.
- **Disposition required:** fixed in the feature plan and in the task order.

### 🟡 M2 — The title is not redacted

- **Problem:** the plan hashed and sent "the redacted title and description".
- **Evidence:** `listing_text.sql` redacts the description only; a title names the property
    type and usually a street.
- **Impact:** unredacted text and a location would leave Silver, against the plan's own
    constraint.
- **Recommendation:** only the description is hashed and sent. Both meanings are stated in the
    description; the title is generated by the portal.
- **Disposition required:** fixed in the feature plan and in tasks 1.4, 2.1 and 3.1.

### 🟡 M3 — Precision on an enriched sample is not the population's

- **Problem:** the gate was a precision of 0.90 on 200 texts stratified by keyword hits.
- **Evidence:** auctions are about 0.9% of sale listings (153 keyword hits). A sample with 40
    of them is 20% auctions. One false positive among the 35 sampled texts of stratum F stands
    for 274 listings, more than all true auctions.
- **Impact:** a label could pass the gate and be wrong more often than right on real data.
- **Recommendation:** the evaluation set stores each text's stratum and the stratum's
    population; the command reports precision and recall raw and weighted by population; the
    gate uses the weighted values. For `is_auction`, task 4.3 adds a direct check after the
    backlog: the user reads the model's auctions that lack the keyword, at most 30.
- **Disposition required:** fixed in the feature plan and in tasks 1.2, 1.5, 1.6 and 4.3.

### 🟡 M4 — Labels without definitions

- **Problem:** `renovation_state` had five values and no rule for a kitchen renewed alone, for
    "ready to move in", or for a text that states two things.
- **Evidence:** the plan's contract section.
- **Impact:** the hand labels and the prompt would measure two different things, and the
    evaluation would report the difference as model error.
- **Recommendation:** one definition per value and a decision order, used word for word in the
    mail to the labeller and in the prompt.
- **Disposition required:** fixed in the feature plan; task 1.3 puts the text into the prompt.

### 🟡 M5 — The labeller would start itself

- **Problem:** the start rule was "a Silver task stops with exit code 0" on a cluster that
    will run the labeller too.
- **Evidence:** `infra/silver/alerts.tf` shows the existing rule pattern: it matches by
    cluster.
- **Impact:** a finished labeller starts the next one, without end.
- **Recommendation:** the rule matches the Silver task definition's family. The failure rule
    stays as it is and covers the labeller; its mail text gains one sentence for it.
- **Disposition required:** fixed in the feature plan and in task 4.1, whose acceptance
    criteria name the pattern.

### 🟡 M6 — What one `label_version` holds, and two smaller integrity points

- **Problem:** the model id was a task argument outside `label_version`; the hash was computed
    on two sides; a run before the first stored part was undefined.
- **Evidence:** the plan's contract section.
- **Impact:** answers of two models could mix under one version; a hash mismatch would leave
    every label null without an error.
- **Recommendation:** a run refuses to write when stored answers of its version carry another
    model id; both sides hash with DuckDB's `sha256`, pinned to `hashlib` by a test; a run with
    the argument and no part writes null labels; an empty description is never sent.
- **Disposition required:** fixed in the feature plan and in tasks 2.1, 2.2, 3.1 and 3.2.

### 🟢 L1 — Sequential requests

- **Evidence:** about 17,000 texts once, then about 750 a week.
- **Suggestion:** no thread pool. Task 1.6 records seconds per text.
- **Benefit:** no concurrency to test; throttling stays a rare case.

### 🟢 L2 — The synthetic landing does not change

- **Evidence:** it has no rent listing, and the unit tests of the statement build their own
    rows.
- **Suggestion:** removed from the files. Applied.
- **Benefit:** no pinned expectation of another test moves.

### 🟢 L3 — The estimate in the registry

- **Evidence:** the plan said about 16 hours and the registry about 18.
- **Suggestion:** both say about 20 hours after M1 and M3. Applied.
- **Benefit:** one number.

## Alternatives considered

- **Keyword rules instead of a model** — $0 and reproducible. Trade-off: "reforma" also
    appears in "needs no renovation"; the stratum counts show 254 listings with both a
    needs-renovation and a renovated keyword. Verdict: stick with the plan; the evaluation
    reports the keyword strata, so the comparison is visible.
- **Label only listings whose `condition` is `good`** — about a fifth fewer requests.
    Trade-off: the model's reading could not be compared with the portal's own `condition`,
    which is the cheapest plausibility check there is. Verdict: stick with the plan; the saving
    is about a dollar once.
- **Batch inference for the backlog** — half the price. Trade-off: a second code path for
    about $2.50, once. Verdict: stick with the plan.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| The model fails a threshold | Med | Med | Task 1.6 gates the build; the user decides per label | User; the evaluation record |
| Rare-label precision is unproven by the sample | High | Med | Weighted values, and the direct check of task 4.3 | User; task 4.3 |
| Residual personal data in a description reaches the model | Low | Med | Redaction `r2`; invocation logging off, verified in task 4.3 | Operator |
| Cost runs away | Low | Low | The per-run cap; the start rule names one task definition | Operator; tokens in every report |
| Two runs at once label a text twice | Low | Low | Silver takes the newest answer; the cap bounds the cost | — |
| One person's labels are the ground truth | High | Low | Definitions and a decision order; disagreements are read, not only counted | User; task 1.6 |

## Effort check

- **Plan estimate:** L (~16h)
- **Reviewer estimate:** L (~20h) — confidence Medium
- **Why it differs / hidden complexity:** the evaluation command with weighted measures, the
    infrastructure's two roles and rule, and two operator tasks with paid runs.

## Reuse & conflicts

- **Reuse:** the set-difference pattern of `src/silver/ingest.py`; `_silver_columns` and the
    whole-table replace of the label tables; the injected clients of `main()` in
    `src/silver/rebuild.py`; the failure rule and topic of `infra/silver/alerts.tf`.
- **Conflict / coordinate with:** FEATURE-009, FEATURE-010 and FEATURE-011 change
    `process_landing`; phase 3 starts after they are merged. Phases 1 and 2 create new files
    only and can start once this plan is on `main`.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-012-technical-plan.yaml`
- **Tasks:** 13; dependency graph and critical path verified yes
- **TDD contract:** RED failures and commands verified yes
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `terraform-validate`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** M1 to M6 fixed in the feature plan and the technical plan
- **Optional:** L1 to L3 applied
- **Accepted residual risk:** the model's quality until task 1.6; the precision of `is_auction`
    until task 4.3
- **Technical plan:** emitted

## Next step

Task 1.2 starts now: the operator draws the sample and mails it to the user. Run
`/implementer FEATURE-012` for phase 1 once this plan is on `main`.

---

# Re-review — the amendment of 2026-11-20

**Reviewer:** `reviewer` · **Date:** 2026-11-20 · **Plan:** [FEATURE-012](../plans/FEATURE-012-text-labels.md)
**Verdict:** ✅ Approved

The review above stays as it was written. This part covers only what the amendment changes: the
Claude API instead of Amazon Bedrock, and a labeller that submits a message batch in one run and
collects it in a later one.

## Summary

The amended design is sound. What is open is read from what is stored (a batch's record and its
part), the evaluation and the labeller send one request built by one function, and the secret
enters the task only as a reference. Seven corrections were needed, R1 to R7; all follow from the
repository, from the provider's documentation or from the account's state on the day, and all are
folded into the plan and the technical plan. Tasks 1.7 to 1.9 can start; the paid part of task
1.6 waits for the user's key.

## Evidence checked

- **Plan and dependencies:** the amended plan and ADR 0005 (uncommitted on
    `feature/text-labels/integration`, which is 3 commits ahead of its remote and 31 behind
    `main`); the implementation note with both Bedrock runs; technical plan version 1.0.
- **Code, tests, data, and configuration:**
    - `src/enrichment/prompt.py`: `SYSTEM_PROMPT` says "record two labels with the tool
        record_text_label. Answer only through the tool."; `tests/test_enrichment_labeler.py`
        asserts that the prompt names the tool and that the version is `tl2`.
    - `src/enrichment/bedrock.py`: `MAX_OUTPUT_TOKENS = 200`, a forced tool choice.
    - `src/enrichment/evaluate.py`: the weight of a text is its stratum's population divided by
        the stratum's texts in the file, so 30 more texts in stratum F lower its weight from 261
        to 141 without a code change.
    - `infra/silver/ecs.tf`: the task has a public address and its security group allows
        outbound HTTPS to any address, so the route to the Claude API is open; the execution
        role may write logs only.
    - `infra/silver/alerts.tf`: the failure rule matches every stopped task of the cluster with
        a non-zero exit code, so it covers the labeller.
    - `docs/silver-rebuild.md`: a scheduler queues one Silver start a day at 05:30 UTC; a run
        with nothing pending exits 0 within a minute and writes nothing.
    - `requirements.txt`: `anthropic` is not installed; Python is 3.12.
    - `python dev/tools/validate_workflow.py` and `python dev/tools/check_leaks.py` pass with
        the amended artifacts.
- **Evidence reviewed:**
    - The provider's documentation as the `claude-api` skill holds it (cached 2026-11-03):
        list prices of $0.10 / $0.50 (Claude Haiku 5.5, prompts up to 100,000 tokens) and
        $2 / $10 (Claude Sonnet 5.5) per million tokens; a batch costs half, holds up to 100,000
        requests or 256 MB, ends within 24 hours and keeps its results for 29 days; structured
        outputs exist for both models; Claude Sonnet 5.5 rejects a forced tool choice; both
        models reject non-default sampling parameters; both think by default, the thinking
        counts toward the output limit, and a response may begin with thinking blocks.
    - The plan's cost table recomputed from 2,000 input and 500 output tokens per text: $0.73 a
        month and $3.88 for the backlog with Claude Haiku 5.5, $14.63 and $77.56 with Claude
        Sonnet 5.5, $0.31 and $6.21 for three direct evaluation runs of 230 texts. The table is
        right. It counts 17,236 texts where about 15,700 are distinct, which errs on the safe
        side.
    - The provider's page on API credits (read 2026-11-20) and the account's billing page as
        the user showed it: a Team plan since 2026-11-17, so the credit can be linked from
        2026-11-24; the Console organization exists and holds no credit.
- **Unverified assumptions:**
    - A batch accepts the request with a structured output and a 64-character `custom_id`, and
        the organization's limits admit it. Owner: operator. Verification point: the batch of
        one text in task 1.6, before phase 2 is built (R3).
    - Claude Haiku 5.5 passes the gate for `renovated`. Owner: operator and user. Verification
        point: task 1.6.
    - Tokens per text, above all the thinking of Claude Sonnet 5.5 at its default effort.
        Owner: operator. Verification point: task 1.6 replaces the estimate.
    - The exact names of the SDK's batch fields. Owner: implementer. Verification point: task
        1.9 takes them from the pinned SDK's types.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | The gate now needs three passing runs; after R5 the lag is stated as it is |
| Repository and dependency assumptions match reality | Pass | After R1, R2 and R6 |
| Supporting evidence is reproducible and sufficient | Pass | Prices and limits from the documentation; the batch path is proven in task 1.6 (R3) |
| Design is the simplest adequate option | Pass | Two stored objects are the whole state; batches are the user's decision |
| TDD slices and validation commands are executable | Pass | Fake client and fake batches; no test calls the service or reads the key |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | After R4 and R7; the key is a reference by name |
| Operations, recovery, migration, and cost are addressed | Pass | After R4 and R5; about $0.10 a month on AWS, the rest from the credit |

## Strengths

- A batch is open from its record until its part exists. A crashed run, a repeated run and a run
    that finds an unfinished batch all need no extra rule.
- One function builds the request for the evaluation and for the batch, so the gate measures what
    operation sends.
- The measurement problem of 2026-11-19 is answered where it arose: more texts in the stratum
    whose single text decided the value, and three runs instead of one.
- The key is created by the user, named by a variable and injected by ECS; no Terraform state, no
    argument and no log holds it.

## Findings

### 🟡 R1 — The prompt tells the model to use a tool the request does not have

- **Problem:** the amendment keeps `SYSTEM_PROMPT` unchanged and asks for the answer as a
    structured output, not through a tool.
- **Evidence:** `src/enrichment/prompt.py` lines 32 to 33; the amendment's section "The Claude
    API request".
- **Impact:** the gate would measure a model that is told to call something that does not exist,
    and the prompt would change after the gate, without a new measurement.
- **Recommendation:** task 1.7 rewords that one sentence so that it names no tool and sets
    `LABEL_VERSION` to `tl3`, before the gate is measured. The Bedrock request forces its tool
    and works with the new wording.
- **Disposition required:** fixed in the feature plan and in task 1.7.

### 🟡 R2 — The credit described is not the user's

- **Problem:** the plan names $100 on a Max plan and treats the change to a Team plan as a future
    risk.
- **Evidence:** the account's billing page on 2026-11-20: a Team plan since 2026-11-17.
- **Impact:** the paid evaluation was planned for a credit that cannot be linked before
    2026-11-24.
- **Recommendation:** the plan states the Team credit ($100 for each Premium and $20 for each
    Standard seat, pooled) and the date. Either model's backlog fits one month of it. The
    evaluation may run earlier on a small purchased amount if the user wants it.
- **Disposition required:** fixed in the feature plan.

### 🟡 R3 — The batch path is first tried after everything is built on it

- **Problem:** the evaluation sends direct requests, and the first batch is sent in task 4.3.
- **Evidence:** the plan's assumption about batches and task 4.3.
- **Impact:** if a batch refused the structured output, the `custom_id` or the organization's
    limits, phases 2 to 4 would be built before anyone knew.
- **Recommendation:** task 1.6 sends and collects one batch of one text with the code of task
    1.9, before the gate is measured. It costs a fraction of a cent.
- **Disposition required:** fixed in the feature plan and in task 1.6, which now depends on 1.9.

### 🟡 R4 — A used-up credit would not fail a run

- **Problem:** the plan says a run fails and alerts when the credit ends. With batches the
    failure returns later, as errored requests of a collected batch, in a run that exits 0.
- **Evidence:** the amended design; `infra/silver/alerts.tf` alerts on a non-zero exit code only.
- **Impact:** labelling could stop for weeks while every run reports success and submits the
    same texts again.
- **Recommendation:** a run writes its parts and then exits 1 when a collected batch held
    requests and none succeeded. A single failed request is counted and does not fail the run.
- **Disposition required:** fixed in the feature plan and in tasks 2.2, 2.3 and 4.1.

### 🟡 R5 — The stated lag does not hold at the end of a week

- **Problem:** the plan promises a label with the second Silver run after its text was seen.
- **Evidence:** the acquisition side writes no commit between the end of a week's collection and
    Sunday 00:00 UTC; the daily Silver start finds nothing pending and writes no table.
- **Impact:** the answers for a week's last commits are collected within a day and reach
    `listing_text_label` only with the next landing commit, some days later. A reader of the
    runbook would expect hours.
- **Recommendation:** accept and document it. A Silver run that rewrites the table when only the
    answers changed would be a second kind of pending work, for labels nobody waits for; a manual
    backfill writes the table at once when someone does.
- **Disposition required:** accepted with this rationale; stated in the feature plan and in
    tasks 4.2 and 4.3.

### 🟡 R6 — The output limit and the reading of an answer were left open

- **Problem:** the plan asks for "an explicit output limit that leaves room for the model's
    thinking" and does not say how a response becomes an answer.
- **Evidence:** the Bedrock labeller's limit is 200 tokens; the candidate models think by
    default, and thinking counts toward the limit.
- **Impact:** with a small limit a response ends before its answer, the text stays pending and
    is sent again by every run.
- **Recommendation:** a limit of 4,096 tokens; the answer is the one text block, chosen by type;
    a response that did not end its turn is an invalid answer.
- **Disposition required:** fixed in the feature plan and in task 1.7.

### 🟡 R7 — What the provider keeps

- **Problem:** the privacy section says where the text goes and not how long it stays there.
- **Evidence:** a batch's results are readable at the provider for 29 days; on Bedrock nothing
    was kept, and the first review relied on that.
- **Impact:** a statement about personal data that is incomplete, in a public repository.
- **Recommendation:** the plan states the 29 days, and the runbook cites the provider's retention
    terms.
- **Disposition required:** fixed in the feature plan and in task 4.2. The user's decision to
    send the redacted text to the provider stands.

### 🟢 L4 — The effort is the model's default

- **Evidence:** the default effort is medium for Claude Haiku 5.5 and high for Claude Sonnet 5.5,
    so the larger model also thinks more per text than the estimate of 500 output tokens assumes.
- **Suggestion:** none before task 1.6. If the measured tokens of Claude Sonnet 5.5 make its
    backlog exceed a month's credit, a named effort is a new `LABEL_VERSION` and a new run.
- **Benefit:** the decision is made on measured tokens.

### 🟢 L5 — A text that fails every time

- **Evidence:** an errored, refused or invalid request leaves its text pending, and every run
    submits it again.
- **Suggestion:** accepted; the report counts such requests and one text costs a fraction of a
    cent a run. A list of texts to skip would be a second state.
- **Benefit:** no code for a case not yet seen.

## Alternatives considered

- **Direct requests in the labeller** — one flow instead of two. Trade-off: twice the price and a
    task that runs as long as the texts take. Verdict: the user chose batches on 2026-11-20; not
    reopened.
- **The provider's list of batches as the state** — no record to write. Trade-off: the state
    would live outside the lake, mix with other uses of the same organization and vanish with
    the provider's retention. Verdict: stick with the plan.
- **Rewrite the table when only answers changed** — see R5. Verdict: not now.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| The gate passes on a sample that cannot prove it | Med | Med | 65 texts in the largest stratum and three runs; the runbook gives the measured values with the sample's size | User; task 1.6 |
| The evaluation labels are a model's, not a person's | High | Low | Recorded in the implementation note; the user declined a human check | User |
| The credit ends or the plan changes | Low | Med | R4 makes the run fail; purchased credit or the Bedrock labeller is the way back | Operator; the failure mail |
| The key leaks | Low | Med | One SecureString, one role that reads it, revocation in the Console | User |
| Residual personal data reaches the provider and stays 29 days | Low | Med | Redaction `r2`; R7 states the retention | Operator |
| Labels of a week's last commits arrive days later | High | Low | R5: documented; a manual backfill writes the table | Operator |
| A crash between submitting and recording leaves a batch nobody collects | Low | Low | Its texts are submitted again, at most one capped batch | — |

## Effort check

- **Plan estimate:** L (~20h), not changed by the amendment
- **Reviewer estimate:** L (~26h) — confidence Medium
- **Why it differs / hidden complexity:** three new tasks in phase 1 (about 3.5 hours), a second
    stored object and two flows in phase 2, and a longer operator task 1.6. About 6.5 hours are
    done.

## Reuse & conflicts

- **Reuse:** `label_from_answer` and `LabelResult` of `src/enrichment/labeler.py`; the evaluation
    command as it is; the failure rule and topic of `infra/silver/alerts.tf`; the Silver task's
    network configuration.
- **Conflict / coordinate with:** `main` is 31 commits ahead of the integration branch
    (FEATURE-011 and others) and is merged into it before task 3.1; `CLAUDE.md` may conflict.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-012-technical-plan.yaml`, version 2.0
- **Tasks:** 17 (5 done, 1 in progress, 11 not started); dependency graph and critical path
    verified yes
- **TDD contract:** RED failures and commands verified yes
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `terraform-validate`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** R1 to R4, R6 and R7 fixed in the feature plan and the technical
    plan; R5 accepted and documented
- **Optional:** L4 and L5, no change
- **Accepted residual risk:** the gate's statistical weight; the lag at the end of a week; a
    text that fails every time; the provider's retention under the user's decision
- **Technical plan:** emitted

## Next step

Commit the amendment to the integration branch with the user's approval, then run
`/implementer FEATURE-012` for tasks 1.7 to 1.9. The paid part of task 1.6 waits for the user:
link the credit (from 2026-11-24) or buy a small amount, create the key and store it in the
Parameter Store.
