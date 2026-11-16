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
    FEATURE-010 and FEATURE-011 are planned and reviewed after this plan. Phases 1 and 2 touch no file
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
