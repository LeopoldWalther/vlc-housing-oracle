# FEATURE-012 — Labels read from the description by a language model

**Status:** 🔵 Planned · **Effort:** L (~16h) · **Priority:** Medium
**Branch root:** `feature/text-labels` · **Created:** 2026-11-16 · **Updated:** 2026-11-16

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-012.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-012-technical-plan.yaml`.

## Objective

Read two meanings that only the free text of a sale listing states into a Silver table,
`listing_text_label`: how far the home is renovated, and whether the sale is an auction. Claude
Haiku 5.5 on Amazon Bedrock reads each redacted description once, for about a dollar a month; its
answers are stored as immutable inputs, so Silver stays reproducible and a backfill costs nothing.

## Context

- **Current state:**
    - `listing_text` holds the redacted title and description of every listing with a detail
        record (redaction `r2`).
    - `listing.condition` has three values. Every Silver table is a pure DuckDB transformation
        of landing; nothing in the repository calls a model.
- **Problem** (aggregate counts on Silver of 2026-11-15, by keyword search):
    - `condition` is `good` for 13,892 of 17,236 sale listings. The text calls 2,440 of them
        freshly or fully renovated and 485 in need of renovation. Fix-and-flip needs exactly
        this distinction.
    - About 153 sale listings mention an auction; in the city their median is 1,254 against
        3,368 EUR/m².
    - Keyword search is the evidence, not the solution: "reforma" also appears in "needs no
        renovation".
- **Constraints:**
    - User decisions of 2026-11-16: the model is reached through Amazon Bedrock in the project's
        own account; the redacted text may be sent to it; the first plan at about $26 a month
        was too expensive, so the model is Claude Haiku 5.5 through Bedrock's global inference
        profile, which may process the text outside the EU.
    - Only redacted text leaves Silver, and no listing identifier with it.
    - A model's answer is not reproducible, so it must not be recomputed by a Silver run.
    - Tests never call a model.

## Scope

- **In scope:**
    - ADR 0005: model-derived labels as immutable inputs beside landing;
    - an evaluation on about 200 hand-labelled sale texts that shows whether the model is good
        enough;
    - the labelling command, its results under `enrichment/v1/text_label/`, and its own task;
    - the Silver table `listing_text_label`.
- **Out of scope:**
    - labels the feature list already states (FEATURE-010);
    - the rental term (long-term, seasonal, room) and the deposit: a sample of 700 stored pages
        shows that the page states them, on 59 and 127 of 188 rent pages, so the acquisition
        side reads them without a model;
    - rent listings altogether: no label of this feature applies to them;
    - fees, taxes and other numbers from text: unmeasured, and a wrong number is worse than a
        missing one;
    - an agent with tools, memory or a conversation: this is one model call per text;
    - batch inference for the backlog: named as the cheaper alternative, not built;
    - prompt tuning beyond what the evaluation needs.
- **Users / consumers:** fix-and-flip (renovation state) and fair-price estimation (auctions
    excluded).

## Dependencies

- **Needs:** FEATURE-006 (redaction `r2`) — complete. Implemented after FEATURE-009 to
    FEATURE-011, which change the same function.
- **Unblocks:** the Gold datasets of the two use cases above.

## Open assumptions

- **Assumption:** a current Claude model reads these meanings well enough. — **Evidence:**
    unvalidated. — **If wrong:** phase 1 measures it before anything is built on it; a label
    that fails its threshold is dropped from the feature.
- **Assumption:** the model is available to the account. — **Evidence:** the account lists
    Claude Haiku 5.5 as a global inference profile only, and EU profiles for Claude Sonnet 5.5
    and Claude Opus 5.5 (checked 2026-11-16). — **If wrong:** N/A, observed.
- **Assumption:** the cost is as estimated below. — **Evidence:** the texts total 42.6 million
    characters over 21,357 distinct descriptions (median 1,683 characters, maximum 7,894).
    Tokens per text and Bedrock's prices are estimates. — **If wrong:** phase 1 measures tokens
    on 200 texts and reads the current Bedrock price; the per-run cap bounds every run.
- **Assumption:** Bedrock returns schema-valid structured answers for these models. —
    **Evidence:** unvalidated in this account. — **If wrong:** every answer is validated in code
    anyway, and an invalid one is not stored.

## Design

- **Primary approach.** Two halves, shaped like acquisition and Silver:
    - **The labeller** is a command, `python -m src.enrichment.text_labels`. It reads Silver's
        `listing_text` for sale listings, hashes each redacted title and description (SHA-256),
        and takes the hashes that have no stored answer under the current `label_version`: a set difference,
        like the ingest log. For each it makes one request and writes the answers in parts of a
        few hundred rows to
        `enrichment/v1/text_label/label_version=<v>/run=<run_id>/part-<n>.parquet`. Parts are
        written once and never replaced; a crash loses at most one part.
    - **Silver** gains `--text-labels <path>`. With it, a run joins `listing_text` to the stored
        answers by hash and replaces `listing_text_label` whole. Silver remains a pure function
        of landing and the stored answers.
- **Deployment.**
    - The labeller runs as a second ECS task definition on the existing cluster and image,
        started by an EventBridge rule when a Silver task stops with exit code 0.
    - Its role may read Silver, write the enrichment prefix and invoke the chosen inference
        profile. The model id and the per-run cap are task arguments.
    - New answers reach `listing_text_label` with the next Silver run, a few hours later.
- **Contracts and invariants:**
    - Stored answer: `text_sha256`, `label_version`, `model_id`, `renovation_state` (`new_build`,
        `renovated`, `original`, `needs_renovation`, `unknown`), `is_auction`, `labeled_at`, `input_tokens`,
        `output_tokens`.
    - `listing_text_label`: one row per listing with a text row; the two labels (null until
        the text is labelled, and always null for a listing that is not a sale),
        `label_version`, `model_id`, `labeled_at`, `transformation_version`.
    - `label_version` names the prompt and the schema. A new version labels every text again
        and leaves the old answers in place.
    - An answer outside the schema is counted and not stored; the text stays pending.
    - No text and no listing id is written to a log.
- **OOP / SOLID:** one protocol, `TextLabeler`, with a Bedrock implementation and a fake for
    tests. The model is a paid external boundary, which is what justifies the interface.
- **Patterns:** none beyond that injected dependency.
- **Deliberately rejected complexity:**
    - **Amazon Bedrock AgentCore** (Runtime or Harness): it hosts agent loops with tools,
        sessions and memory. This job has none of them.
    - calling the model inside the Silver run: it would make Silver irreproducible and a
        backfill expensive;
    - the labeller writing the Silver table itself: two writers to one catalog, to save a lag
        of hours on attributes that rarely change;
    - a vector store, a prompt framework, a second SDK: `boto3` is already in the image.

## Approach

### Phase 1 — Is the cheap model good enough (gated by approval)

- [ ] **1.1 ADR 0005 and prices.** The decision and layout; the current Bedrock price of the
    model.
- [ ] **1.2 Evaluation set.** A private sample of 200 sale texts, stratified by `condition` and
    by the keyword hits, labelled by hand and stored outside the repository.
- [ ] **1.3 Evaluation.** RED: `python -m src.enrichment.evaluate` with a fake labeller prints
    agreement per label and value. Then, with approval, one run with Claude Haiku 5.5 (about
    six cents). A label is kept if its decisive values (`renovated`, `needs_renovation`,
    `is_auction`) reach a precision of 0.90 and a recall of 0.80. If one fails, the user decides
    between dropping the label and a larger model for it. Recorded as counts.

### Phase 2 — The labeller

- [ ] **2.1 Pending texts.** RED: sale texts without an answer are pending and rent texts never
    are; identical texts are one; an answer under an older `label_version` does not count.
- [ ] **2.2 One run.** RED: with a fake labeller a run stores one answer per pending text in
    parts, stops at the cap, and a second run labels nothing twice; an invalid answer is
    counted and left pending; a failing request after the retries leaves earlier parts in place.
- [ ] **2.3 The Bedrock labeller.** RED against a stubbed client: the request carries the
    prompt, the text, the schema and an explicit output limit; throttling is retried. No test
    calls the service.

### Phase 3 — Silver

- [ ] **3.1 The table.** RED: a listing whose text has an answer carries it; one without has
    nulls; a rent listing has nulls even when its text has an answer; of two answers for one
    hash the newest of the current version wins.
- [ ] **3.2 Pipeline.** RED: with the argument the run writes the table; without it none;
    stepwise commits equal a backfill over the same stored answers.

### Phase 4 — Operation

- [ ] **4.1 Infrastructure.** Task definition, role, rule and failure alert; `terraform
    validate` and a plan that adds only these.
- [ ] **4.2 Documentation.** Runbook: the flow, the cap, a new `label_version`, the cost.
- [ ] **4.3 Backlog and first runs (operator, with approval).** Label the backlog under the
    cap, then counts only: texts labelled, tokens, cost, label distribution, and agreement with
    the keyword counts above. Recorded in
    `dev/plans/implementations/FEATURE-012-text-labels.md`.

## Files

- **Create:** `docs/adr/0005-model-derived-labels.md`, `src/enrichment/__init__.py`,
    `src/enrichment/text_labels.py`, `src/enrichment/bedrock.py`, `src/enrichment/evaluate.py`,
    `src/enrichment/prompt.py`, `src/silver/sql/listing_text_label.sql`, `infra/silver/labels.tf`.
- **Change:** `src/silver/rebuild.py`, `requirements.txt` (`boto3` pinned as a direct
    dependency), `src/synthetic/landing.py`, `docs/silver-rebuild.md`, `README.md`, `CLAUDE.md`.
- **Tests:** `tests/test_enrichment_text_labels.py`, `tests/test_enrichment_bedrock.py`,
    `tests/test_enrichment_evaluate.py`, `tests/test_silver_listing_text_label.py` (new),
    `tests/test_silver_cli.py`.

## Test strategy

- **Unit:** pending set, cap, parts, invalid answers, request shape, the join.
- **Contract / fixtures:** the stored answer's schema and the table's columns; synthetic texts.
- **Integration:** labeller and Silver run against local directories and a SQLite catalog with
    the fake labeller.
- **Retries / idempotency / evolution:** a repeated run labels nothing twice; a new
    `label_version`; incremental equals backfill.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`,
    `terraform -chdir=infra/silver validate`.
- **Manual:** the evaluation runs of 1.3 and the backlog of 4.3.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract. Model answers are a new,
    immutable input written by this repository's own command.
- **Privacy / security:** redacted text is sent to Bedrock in the account, without ids, through
    the global inference profile, so it may be processed outside the EU (user decision). The
    text is the redacted wording of a public advertisement. Model-invocation logging stays off,
    because it would store the texts. The role is limited to one profile and two prefixes.
- **Observability:** the run reports texts labelled, invalid answers, tokens and pending texts;
    the failure alert covers the new task.
- **Failure and recovery:** a failed run leaves stored parts valid and the rest pending; the
    next run continues. Silver never depends on the labeller having run.
- **Migration / compatibility:** additive; no backfill.

## Estimated monthly cloud cost

Token counts are estimates (about 1,500 input and 300 output tokens per text), and the prices are
Anthropic's list prices of November 2026 as a stand-in for Bedrock's, which task 1.1 reads.

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Bedrock, Claude Haiku 5.5 | $0.10 / $0.50 per million tokens | 750 new sale texts a week | ~$1.00 |
| ECS Fargate | ARM64, 0.25 vCPU | a few minutes a day | ~$0.10 |
| **Total (new AWS components)** | | | **~$1.10/month** |

- **One-time backlog:** 17,236 sale listings, about $5.
- **Cost drivers & cheaper alternatives:** output tokens. The first plan sent every text to
    Claude Sonnet 5.5 for about $26 a month; the same job with the smaller model and without
    rent texts costs about a twenty-fifth. Keyword rules would cost nothing and be less exact.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] The evaluation is recorded, and each kept label met its threshold with the chosen model.
- [ ] Every listing with a text row has one row in `listing_text_label`.
- [ ] No text is labelled twice under one `label_version`, and a run never exceeds its cap.
- [ ] A Silver backfill makes no model request and reproduces the table.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak, infrastructure and workflow checks pass
- [ ] The flow, the cost and the versioning are documented
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (user, before 1.2):** who labels the 200 texts by hand, and when?
- **Question (user, before 1.3 and 4.3):** approve each paid run and its upper bound.
- **Question (user, after 1.3, only if a label fails):** drop the label, or use a larger model
    for it.
- **Risk:** residual personal data in a description reaches the model. — *Mitigation:*
    redaction `r2` left two ordinary words in 19,038 descriptions, and nothing is logged.
- **Risk:** a label is read as a fact. — *Mitigation:* it lives in its own table with model and
    version, and the documentation gives the measured precision.
- **Risk:** cost runs away. — *Mitigation:* the per-run cap, tokens in every report, and the
    labeller never retries a stored text.

## Progress log

- **2026-11-16** — Plan created from the exploration of 2026-11-15; the fourth of four features.
    User decisions: Bedrock in the own account; redacted text may be sent.
- **2026-11-16** — Made cheaper after the user's objection to about $26 a month: Claude Haiku
    5.5 through the global profile, sale texts only, about $1 a month. The rental term left the
    feature, because stored pages show that the page states it.
