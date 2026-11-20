# FEATURE-012 — Labels read from the description by a language model

**Status:** 🟡 In progress · **Effort:** L (~26h) · **Priority:** Medium
**Branch root:** `feature/text-labels` · **Created:** 2026-11-16 · **Updated:** 2026-11-20

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-012.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-012-technical-plan.yaml`.

## Objective

Read two meanings that only the free text of a sale listing states into a Silver table,
`listing_text_label`: how far the home is renovated, and whether the sale is an auction. A Claude
model reads each redacted description once through the Claude API, paid from the API credit that
the user's Claude plan includes, so nothing is billed; its answers are stored as immutable inputs,
so Silver stays reproducible and a backfill costs nothing.

> **Amended 2026-11-20** (to be re-reviewed): the model is reached through the Claude API instead
> of Amazon Bedrock, and the labeller sends its texts as a message batch and collects the answers
> in a later run. The amendment adds tasks 1.7 to 1.9, changes task 1.6 and the unstarted tasks
> of phases 2 and 4, and leaves the stored answer's columns, the Silver table and every finished
> task as they are.
>
> **Re-reviewed and approved 2026-11-20** with corrections R1 to R7 of the review, folded in
> below and marked with their number.

## Context

- **Current state:**
    - `listing_text` holds the title and the redacted description of every listing with a
        detail record (redaction `r2`). The title is verbatim and usually names a street.
    - `listing.condition` has three values (`good` 13,892, `needs_renovation` 2,069, `new`
        1,233 of the 17,236 sale listings; 42 have none). Every Silver table is a pure DuckDB transformation
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
    - User decisions of 2026-11-20, which replace the Bedrock decision above: the model is
        reached through the Claude API with an API key of the user's own Console organization,
        paid from the monthly API credit of the user's Claude plan (R2: a Team plan since
        2026-11-17, $100 a month for each Premium seat and $20 for each Standard seat, pooled); the
        redacted text may be sent to Anthropic directly; the user stores the key in the
        Parameter Store himself, and it never passes through a chat, a commit or a log.
        Reasons: Claude Haiku 5.5 is closed to the AWS account (its quota is 0 and the increase
        was refused), Claude Haiku 4.5 on Bedrock costs about seven times the estimate and
        fails the gate for `renovated`, and the credit makes a stronger model free.
    - User decision of 2026-11-20: the labeller uses the Message Batches API. Nothing here is
        urgent, a batch costs half, and work that waits for nobody is what a batch is for.
    - Only the redacted description leaves Silver: no title, no location and no listing
        identifier with it (review M2).
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
    - Bedrock's own batch inference: the model is no longer reached through Bedrock;
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
    **Evidence:** observed in two runs of 200 texts with Claude Haiku 4.5: no invalid answer. —
    **If wrong:** N/A, observed.
- **Assumption (2026-11-20):** the plan's API credit is claimed and covers the job. —
    **Evidence:** Anthropic's page "API credits for Max, Ultra, and Team plans" (read
    2026-11-20): the credit covers the Claude API, arrives monthly, does not roll over, and
    requests stop when it is used up; the plan is never charged. Not yet claimed by the user:
    the Team plan must be seven days old, so the credit can be linked from 2026-11-24 at the
    earliest (R2). — **If wrong:** the Bedrock labeller stays in the code and Claude Haiku 4.5 works there, at
    about $7 a month and without `renovated`.
- **Assumption (2026-11-20):** Claude Haiku 5.5 through the Claude API passes the gate for
    `renovated`. — **Evidence:** unvalidated; Claude Haiku 4.5 reached 0.88 and 0.86. — **If
    wrong:** task 1.6 evaluates Claude Sonnet 5.5 next; if that fails too, the label is dropped.
- **Assumption (2026-11-20):** the Claude API answers in the stored schema for both candidate
    models. — **Evidence:** the documentation lists structured outputs for Claude Haiku 5.5 and
    Claude Sonnet 5.5; Claude Sonnet 5.5 refuses a forced tool, which the Bedrock request uses. —
    **If wrong:** every answer is validated in code anyway, and an invalid one is not stored.
- **Assumption (2026-11-20):** a batch of this size is accepted and ends within a day, and a
    request's `custom_id` may be the 64 characters of the text's hash. — **Evidence:** the
    documentation: up to 100,000 requests or 256 MB per batch, most batches end within an hour
    and every batch within 24 hours, at half the price; the limits of the organization's usage
    tier are unvalidated, and so is a structured output inside a batch. — **If wrong:** task
    1.6 sends one batch of one text before the gate is measured, so this is known before phase
    2 is built (R3); the per-run cap is lowered to what the tier accepts; task 4.3 starts with
    a batch of one text again, from the deployed task.
- **Assumption (2026-11-20):** the labeller task reaches the Claude API from its network. —
    **Evidence:** `infra/silver/ecs.tf`: the task has a public address and its security group
    allows outbound HTTPS to any address; the first run confirms it. — **If wrong:** task 4.1 finds it with the first run
    of one text, before the backlog.

## Design

- **Primary approach.** Two halves, shaped like acquisition and Silver:
    - **The labeller** is a command, `python -m src.enrichment.text_labels`. It reads Silver's
        `listing_text` for sale listings, hashes each non-empty redacted description (SHA-256
        of its UTF-8 bytes, lower-case hex), and takes the hashes that have no stored answer
        under the current `label_version`: a set difference, like the ingest log. Requests are
        made one after another. For each it makes one request and writes the answers in parts of a
        few hundred rows to
        `enrichment/v1/text_label/label_version=<v>/run=<run_id>/part-<n>.parquet`. Parts are
        written once and never replaced; a crash loses at most one part.
    - **Silver** gains `--text-labels <path>`. With it, a run joins `listing_text` to the stored
        answers by hash and replaces `listing_text_label` whole. Silver remains a pure function
        of landing and the stored answers.
    - **The labeller in two steps** (2026-11-20, replaces "one request after another" above).
        A run first **collects**: for every batch it submitted earlier that has ended and has
        no stored part, it reads the results and writes the valid answers as one part,
        `enrichment/v1/text_label/label_version=<v>/run=<batch_id>/part-0.parquet`, also when
        no answer was valid. Then it **submits**: the pending texts, up to the cap, as one
        batch whose `custom_id` per request is the text's hash, and after that a record of the
        hashes under `enrichment/v1/text_label_batch/label_version=<v>/batch=<batch_id>/`.
        The run never waits for a batch. A text is pending when it has no stored answer and is
        in no open batch; a batch is open from its record until its part exists. The record
        and the part are the whole state: there is no table of batches and no stored cursor.
- **Deployment.**
    - The labeller runs as a second ECS task definition on the existing cluster and image
        (the image's entry point is Silver's, so the definition overrides it), started by an
        EventBridge rule when a task of the Silver task definition stops with exit code 0. The
        rule names that definition: a rule on the cluster alone would let a finished labeller
        start the next one without end (review M5).
    - Its role may read Silver and write the enrichment prefix. The API key is a SecureString
        in the Parameter Store that the user creates; the task definition names it as a secret,
        so ECS puts it into the task's environment and it appears in no Terraform state, no
        argument and no log. The provider, the model id and the per-run cap are task arguments.
    - The credit is a second, hard cap beside the per-run cap: when it is used up, requests
        fail, the run fails and alerts, and the texts stay pending until the next month.
    - New answers reach `listing_text_label` with the next Silver run, a few hours later.
        With batches (2026-11-20) it is the run after that: a text seen by run N is submitted
        after N, collected after N+1 and in the table with N+2.
    - R5: that holds while landing commits arrive, every three hours during a week's
        collection. The daily catch-up run starts the labeller too, so a batch is collected
        within a day, but a Silver run with nothing pending writes no table. The answers for
        the last commits of a week therefore reach `listing_text_label` with the next landing
        commit, on the following Sunday. Accepted: nothing waits for a label, and a manual
        backfill writes the table at once when someone does.
    - R4: a run in which a collected batch has no succeeded request (the credit is used up,
        the key is revoked, the model is refused) writes its parts and then exits 1, so the
        failure alert fires. Single failed requests are counted and do not fail the run.
- **Contracts and invariants:**
    - Stored answer: `text_sha256`, `label_version`, `model_id`, `renovation_state` (`new_build`,
        `renovated`, `original`, `needs_renovation`, `unknown`), `is_auction`, `labeled_at`, `input_tokens`,
        `output_tokens`.
    - `listing_text_label`: one row per listing with a text row; the two labels (null until
        the text is labelled, and always null for a listing that is not a sale),
        `label_version`, `model_id`, `labeled_at`, `transformation_version`.
    - `label_version` names the prompt, the schema and the model. A new version labels every
        text again and leaves the old answers in place. A run refuses to write when stored
        answers of its `label_version` carry another `model_id`.
    - Silver computes the hash with DuckDB's `sha256` and the labeller with the same function;
        a test pins both to Python's `hashlib` on a text with non-ASCII characters.
    - A Silver run with `--text-labels` and no stored part writes the table with null labels.
        A listing with an empty description is never sent and keeps null labels.
    - Two labeller runs at the same time may label one text twice; Silver takes the newest
        answer of the current version, and the cap bounds the cost.
    - An answer outside the schema is counted and not stored; the text stays pending.
    - Batches (2026-11-20): a part is written once, with the batch's end as `labeled_at`, so
        collecting a batch twice gives the same object and the second write is refused. A
        request that errored or expired has no answer and its text is pending again. A crash
        between submitting and writing the record leaves a batch nobody collects; its texts
        are submitted again, at most one capped batch at half price. A batch whose results
        can no longer be read (29 days after its creation) is closed with an empty part.
    - Nothing but the redacted description and its own hash is sent.
    - No text and no listing id is written to a log.
- **Label definitions** (the same wording for the hand labels and the prompt):
    - `renovation_state` is what the description says about the home as a whole, decided in
        this order:
        1. `needs_renovation`: it needs works or is sold to be renovated ("para reformar",
            "a reformar", "necesita reforma", "para actualizar");
        2. `new_build`: new construction, never lived in;
        3. `renovated`: renovated as a whole, or recently ("reformado", "reforma integral",
            "recién reformado", "a estrenar tras reforma");
        4. `original`: habitable as it is, with no whole renovation stated; a renewed kitchen
            or bathroom alone, "buen estado" and "para entrar a vivir" belong here;
        5. `unknown`: the description says nothing about the state.
    - `is_auction` is true when the home is acquired through an auction or the assignment of
        an auction award ("subasta", "puja", "cesión de remate"), and false otherwise, also
        when the text says it is not an auction.
- **OOP / SOLID:** two narrow protocols at the paid boundary, each with a fake for tests.
    `TextLabeler` answers one text at once and serves the evaluation, with a Claude API and the
    Bedrock implementation of phase 1. `TextLabelBatches` submits texts, tells whether a batch
    has ended and yields its results, and serves the labeller, with a Claude API implementation.
    Both Claude API implementations build their request with the same function, so the gate
    measures the request that operation sends.
- **The Claude API request** (2026-11-20): the system prompt and the answer schema of
    `prompt.py`, unchanged; the answer as a structured output in that schema, not as a forced
    tool; an explicit output limit that leaves room for the model's thinking; no sampling
    parameter and no thinking setting, so the model's defaults are part of `LABEL_VERSION` and
    one request form serves both candidate models. A refusal or an answer outside the schema is
    an invalid answer. R6: the output limit is 4,096 tokens, because the model's thinking counts
    toward it; an answer is read from the text block, never by position, and a response that
    did not end its turn (the limit, a refusal) is an invalid answer. R1: `SYSTEM_PROMPT` told
    the model to answer through a tool, which this request does not have. Task 1.7 rewords that
    one sentence so that it names no tool (the Bedrock request still forces its tool), and
    this is `LABEL_VERSION` `tl3`, before the gate is measured. The SDK's own bounded retries cover throttling and server errors.
    The evaluation sends this request directly, because 230 texts should answer in minutes;
    the labeller sends the same request inside a batch.
- **Patterns:** none beyond that injected dependency.
- **Deliberately rejected complexity:**
    - **Amazon Bedrock AgentCore** (Runtime or Harness): it hosts agent loops with tools,
        sessions and memory. This job has none of them.
    - calling the model inside the Silver run: it would make Silver irreproducible and a
        backfill expensive;
    - the labeller writing the Silver table itself: two writers to one catalog, to save a lag
        of hours on attributes that rarely change;
    - a vector store or a prompt framework;
    - direct requests in the labeller (2026-11-20): one flow less, but twice the price, a task
        that runs as long as the texts take, and throttling to handle;
    - a task that waits for its batch: it would pay for up to a day of idle compute and lose
        the batch when it dies;
    - a state store or a table of open batches: the record and the part say everything;
    - batches for the evaluation: the same request and the same model answer either way;
    - several answers per text and a majority in production: the evaluation is repeated
        instead, and a label is only kept if every repetition passes;
    - removing the Bedrock labeller: it stays for the evaluation, where it produced the two
        recorded runs. The labeller itself supports the Claude API only; a way back to Bedrock
        would be a new task, not a switch.
- **New dependency** (2026-11-20): the `anthropic` SDK, pinned. `boto3` cannot call the Claude
    API, and the SDK brings the request types, the retries and the error classes that a
    hand-written HTTP client would have to copy.

## Approach

### Phase 1 — Is the cheap model good enough (gated by approval)

- [ ] **1.1 ADR 0005.** The decision and the layout.
- [ ] **1.2 Evaluation set (user and operator).** A private sample of 200 distinct sale
    descriptions in eight strata of `condition` and keyword hits, labelled by the user by hand
    from a file sent by mail, and stored outside the repository with each stratum's population.
- [ ] **1.3 Answer, prompt and validation.** RED: an answer inside the schema becomes a label;
    one outside it is refused.
- [ ] **1.4 The Bedrock labeller.** RED against a stubbed client: the request carries the
    prompt, the description, the schema and an explicit output limit; throttling is retried a
    bounded number of times. No test calls the service.
- [ ] **1.5 The evaluation command.** RED: `python -m src.enrichment.evaluate` with a fake
    labeller prints, per decisive value, counts and precision and recall, raw and weighted by
    the strata's populations, and the tokens used; no text is printed.
- [ ] **1.6 Paid evaluation and gate (operator, with approval).** The current Bedrock price,
    then one run with Claude Haiku 5.5 (about six cents). A label is kept if its decisive
    values (`renovated`, `needs_renovation`, `is_auction`) reach a weighted precision of 0.90
    and a weighted recall of 0.80. If one fails, the user decides between dropping the label
    and a larger model for it. Recorded as counts.
    **Amended 2026-11-20:** after 1.7 and 1.8 the gate is measured through the Claude API. The
    largest stratum, where one text stands for 261, is extended by 30 texts first. Claude
    Haiku 5.5 is run three times over the set; a label is kept if it reaches both thresholds
    in each of the three runs. Only if `renovated` fails is Claude Sonnet 5.5 run the same way,
    and the user then decides between the larger model and dropping the label. The model that
    is chosen and its prompt get a new `LABEL_VERSION`: `tl3` of task 1.7 is that version for
    the model the user chooses, and nothing is stored under it before (R1). Before the first
    run one batch of one text is sent and collected (R3). The key is read from the Parameter
    Store into the command's environment and is never printed.
- [ ] **1.7 The Claude API labeller.** RED against a stubbed client: the request carries the
    prompt, the description, the schema as a structured output and an explicit output limit,
    and sets no sampling parameter; a valid answer becomes a label with its tokens; a refusal
    and an answer outside the schema give no label. No test calls the service.
- [ ] **1.8 The evaluation command takes a provider.** RED: `--provider` chooses between the
    two labellers and is required; the report is unchanged.
- [ ] **1.9 The Claude API batches.** RED against a stubbed client: submitting sends one
    request per text with the hash as `custom_id` and the request of 1.7, and returns the
    batch's id; a batch reports whether it has ended and when; the results give, per hash, a
    label with its tokens, and no label for an errored, expired, refused or invalid one.

### Phase 2 — The labeller

- [ ] **2.1 Pending texts.** RED: sale texts without an answer are pending and rent texts never
    are; identical texts are one; an empty description is never pending; an answer under an
    older `label_version` does not count.
- [ ] **2.2 One run.** RED: with a fake labeller a run stores one answer per pending text in
    parts, stops at the cap, and a second run labels nothing twice; an invalid answer is
    counted and left pending; a failing request after the retries leaves earlier parts in
    place; stored answers of another model under the same `label_version` are refused.
    **Amended 2026-11-20, replacing 2.1 and 2.2 where they differ** (with fake batches):
    - **2.1 Pending texts:** as above, and a text in an open batch is not pending.
    - **2.2 Collect and submit:** a run collects every ended batch without a part and then
        submits the pending texts up to the cap, with their record; it never waits; a second
        run submits nothing twice and collects nothing twice; a batch that has not ended is
        left alone; errored, expired and invalid requests are counted and pending again;
        stored answers of another model under the same `label_version` are refused.
    - **2.3 The command:** the stored part and record, their layout and schema; the Claude
        API key comes from the task's environment and is never an argument.

### Phase 3 — Silver

- [ ] **3.1 The table.** RED: a listing whose text has an answer carries it; one without has
    nulls; a rent listing has nulls even when its text has an answer; of two answers for one
    hash the newest of the current version wins; the hash equals `hashlib`'s.
- [ ] **3.2 Pipeline.** RED: with the argument the run writes the table, also when no part is
    stored yet; without it none; stepwise commits equal a backfill over the same stored
    answers.

### Phase 4 — Operation

- [ ] **4.1 Infrastructure.** Task definition, role, rule and failure alert; `terraform
    validate` and a plan that adds only these. **Amended 2026-11-20:** the role has no model
    permission; the task definition takes the API key from a Parameter Store SecureString by
    name, and the execution role may read that one parameter.
- [ ] **4.2 Documentation.** Runbook: the flow, the cap, a new `label_version`, the cost.
- [ ] **4.3 Backlog and first runs (operator, with approval).** Label the backlog under the
    cap, then counts only: texts labelled, tokens, cost, label distribution, and agreement with
    the keyword counts above. The user reads the texts the model calls an auction although
    they do not contain the keyword, at most 30, and the share that are auctions is recorded:
    the evaluation set is too small to prove the precision of a label this rare (review M3). Recorded in
    `dev/plans/implementations/FEATURE-012-text-labels.md`.
    **Amended 2026-11-20:** the first batch holds one text, to prove the key, the network
    route and the tier's limits; the backlog then goes as batches under the per-run cap and
    fits one month of credit with either model.

## Files

- **Create:** `docs/adr/0005-model-derived-labels.md`, `src/enrichment/__init__.py`,
    `src/enrichment/labeler.py`, `src/enrichment/prompt.py`, `src/enrichment/bedrock.py`,
    `src/enrichment/claude_api.py`, `src/enrichment/evaluate.py`, `src/enrichment/text_labels.py`,
    `src/enrichment/batches.py` (the `TextLabelBatches` protocol),
    `src/enrichment/sql/pending_text.sql`, `src/silver/sql/listing_text_label.sql`,
    `infra/silver/labels.tf`.
- **Change:** `src/silver/rebuild.py`, `requirements.txt` (`boto3` and `anthropic` pinned as
    direct dependencies), `infra/silver/ecs.tf`, `infra/silver/variables.tf`, `docs/silver-rebuild.md`,
    `README.md`, `CLAUDE.md`. The `Dockerfile` copies `src/` whole and stays as it is.
- **Tests:** `tests/test_enrichment_labeler.py`, `tests/test_enrichment_bedrock.py`,
    `tests/test_enrichment_claude_api.py`,
    `tests/test_enrichment_evaluate.py`, `tests/test_enrichment_text_labels.py`,
    `tests/test_silver_listing_text_label.py` (new), `tests/test_silver_incremental.py`,
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
- **Privacy / security:** redacted text is sent, without ids, to Anthropic through the Claude
    API, so it leaves the AWS account and may be processed outside the EU (user decision of
    2026-11-20). The text is the redacted wording of a public advertisement. R7: unlike
    Bedrock, the provider keeps what it receives for a time: a batch's requests and answers
    stay readable there for 29 days, and other requests under the retention of its commercial
    terms, which the runbook cites. The API key is the
    feature's one secret: a Parameter Store SecureString that the user writes, read only by the
    task's execution role. The task role is limited to two prefixes.
- **Observability:** the run reports texts labelled, invalid answers, tokens and pending texts;
    the failure alert covers the new task.
- **Failure and recovery:** a failed run leaves stored parts valid and the rest pending; the
    next run continues. An open batch survives a failed run and is collected by the next. Silver never depends on the labeller having run.
- **Migration / compatibility:** additive; no backfill.

## Estimated monthly cloud cost

Amended 2026-11-20. The two Bedrock runs measured about 1,550 input tokens per text with Claude
Haiku 4.5; the newer models count about 30% more, and their thinking adds output tokens, so the
estimate is 2,000 input and 500 output tokens per text. Prices are Anthropic's list prices of
November 2026. Task 1.6 replaces the estimate with measured tokens.

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| ECS Fargate | ARM64, 0.25 vCPU | a few minutes a day | ~$0.10 |
| Parameter Store | one standard SecureString | read once per run | $0.00 |
| **Total (new AWS components)** | | | **~$0.10/month** |

- **External / non-AWS costs:** the Claude API, paid from the plan's monthly credit and
    therefore not billed. Consumption of the credit at 3,250 new sale texts a month:

    | Model | Batch price | Per month | Backlog of 17,236 texts |
    |---|---|---|---|
    | Claude Haiku 5.5 | $0.05 / $0.25 per million tokens | ~$0.75 | ~$4 |
    | Claude Sonnet 5.5 | $1 / $5 per million tokens | ~$15 | ~$78 |

    The evaluation of task 1.6 sends direct requests at twice these prices: under $1 for
    Claude Haiku 5.5 and about $6 for Claude Sonnet 5.5, three runs each.

    When the credit of a month is used up, requests stop and nothing is charged. Without the
    credit the same amounts would be billed.
- **Cost drivers & cheaper alternatives:** output tokens, which include the model's thinking.
    Keyword rules would cost nothing and be less exact. On Bedrock, Claude Haiku 4.5 measured about $7 a month and $35 for the
    backlog.
- **Budget check:** yes.

## Success criteria

- [ ] The evaluation is recorded, and each kept label met its threshold with the chosen model.
- [ ] Every listing with a text row has one row in `listing_text_label`.
- [ ] A repeated run labels no text twice under one `label_version`, and a run never exceeds
    its cap.
- [ ] A Silver backfill makes no model request and reproduces the table.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak, infrastructure and workflow checks pass
- [ ] The flow, the cost and the versioning are documented
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Answered (user, 2026-11-16):** the user labels the 200 texts by hand, from a file sent
    by mail. **Changed the same day:** the user handed the labelling to the assistant, so no
    human labelled the set; see the implementation note for what that means for the gate. The
    user declined a human check of it.
- **Question (user, before 1.3 and 4.3):** approve each paid run and its upper bound.
- **Question (user, after 1.3, only if a label fails):** drop the label, or use a larger model
    for it.
- **Risk:** residual personal data in a description reaches the model. — *Mitigation:*
    redaction `r2` left two ordinary words in 19,038 descriptions, and nothing is logged.
- **Risk:** a label is read as a fact. — *Mitigation:* it lives in its own table with model and
    version, and the documentation gives the measured precision.
- **Risk:** cost runs away. — *Mitigation:* the per-run cap, tokens in every report, and the
    labeller never retries a stored text. The credit ends requests when it is used up, as long
    as the Console organization holds no purchased credit and no auto-reload. A purchased
    amount, such as $5 to run the evaluation before the credit is linked, is spent to its end
    and no further.
- **Risk (2026-11-20):** the credit ends: the plan is cancelled or changed, or the programme
    stops. The link to a Console organization is changed only by the provider's support. — *Mitigation:* runs fail loudly and texts stay pending; the way back is purchased
    credit or the Bedrock labeller.
- **Risk (2026-11-20):** the API key leaks. — *Mitigation:* it exists in the Console and in one
    SecureString only, and a leaked key is revoked in the Console; a workspace of its own with
    a spend limit bounds what it could spend.
- **Question (user, before 1.6 continues):** claim the credit, create the key and store it in
    the Parameter Store.

## Progress log

- **2026-11-16** — Plan created from the exploration of 2026-11-15; the fourth of four features.
    User decisions: Bedrock in the own account; redacted text may be sent.
- **2026-11-16** — Made cheaper after the user's objection to about $26 a month: Claude Haiku
    5.5 through the global profile, sale texts only, about $1 a month. The rental term left the
    feature, because stored pages show that the page states it.
- **2026-11-16** — Reviewed and approved. Corrections folded in: the evaluation needs the model
    call, so the prompt, the Bedrock labeller and the evaluation command move into phase 1;
    only the description is hashed and sent; label definitions; precision and recall weighted
    by the strata; the start rule names the Silver task definition; the model belongs to
    `label_version`. The user labels the evaluation set.
- **2026-11-16** — Task 1.2: the evaluation set is drawn and labelled. The user handed the
    labelling to the assistant; 45 of the 200 texts are marked as borderline. Counts are in
    `dev/plans/implementations/FEATURE-012-text-labels.md`.
- **2026-11-16** — Task 1.6, first run: Claude Haiku 5.5 is not available to the account, so
    Claude Haiku 4.5 was evaluated with prompt `tl1`. `is_auction` and `needs_renovation` pass
    the gate; `renovated` fails on weighted precision (0.88).
- **2026-11-19** — Task 1.6, second run: the user chose a sharper prompt over dropping the
    label. Prompt `tl2` (a change of `src/enrichment/prompt.py`, which task 1.6 describes as a
    new `LABEL_VERSION` and a new run) reaches 0.86: three errors gone, one new one in the
    largest stratum, where one text decides the value, and the model's answers change between
    runs. The decision on `renovated` waits for the model decision, which the user postponed;
    the measurement has to become stable first. The gate stays open.
- **2026-11-20** — Amended by the Architect after the user's decision: the Claude API paid from
    the plan's API credit replaces Bedrock for the gate and for operation. New tasks 1.7 (the
    Claude API labeller), 1.8 (the evaluation command takes a provider) and 1.9 (the Claude
    API batches); task 1.6 and the unstarted tasks of phases 2 and 4 change. The Architect
    first rejected the Message Batches API as a second flow; the user chose it the same day,
    for the price and because the work waits for nobody, so the labeller submits in one run
    and collects in a later one. The amendment goes back to the Reviewer before any of it is
    built.
- **2026-11-20** — Amendment re-reviewed and approved. Corrections folded in: the prompt names
    no tool and becomes `tl3` before the gate (R1); the credit is that of a Team plan and can
    be linked from 2026-11-24 (R2); one batch of one text is sent in task 1.6, before phase 2
    is built (R3); a batch without a succeeded request fails the run (R4); the last answers of
    a week reach the table with the next landing commit (R5); the output limit and the reading
    of an answer (R6); the provider's retention (R7). The technical plan is version 2.0 with
    17 tasks.
