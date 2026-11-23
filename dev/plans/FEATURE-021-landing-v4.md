# FEATURE-021 — Silver follows landing v4, and the contract has one source

**Status:** 🔵 Planned · **Effort:** M (~12h) · **Priority:** Medium
**Branch root:** `feature/landing-v4` · **Created:** 2026-11-22 · **Updated:** 2026-11-22

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-021.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-021-technical-plan.yaml`.

## Objective

Silver reads landing v4, so the four detail fields the acquisition side already publishes reach
`listing_detail` and `listing`, and nothing in this repository reads v3 any more. The landing
version becomes one value in code and one in Terraform, and a version is frozen here by a pin, a
tag and a small golden fixture, so the next version is a short procedure, not a feature.

## Context

- **Current state:**
    - `contracts/landing/` holds v1, v2 and v3. The pin test pins v1 and v3
        (`tests/test_landing_contract.py:14`, `:25`); no tracked file names a v4.
    - The version is hard-wired, not switched: a loader pair per version
        (`src/contracts.py:8-9`, `:22-29`), the commit check (`src/silver/commits.py:49-54`),
        `register_landing_v3` (`src/silver/landing.py:124`), the snapshot summary
        (`src/silver/rebuild.py:334`), the synthetic writer (`src/synthetic/landing.py:397-398`).
        The `--v3` and `--v4` switches the review names belong to the acquisition side's landing
        task; the reader's only argument is the `--landing` prefix.
        Terraform names the prefix four times (`infra/silver/ecs.tf:97`, `:127`, `:194`,
        `infra/silver/trigger.tf:14`).
    - `listing_detail` and `listing` select `*` from the detail records
        (`src/silver/sql/listing_detail.sql:4`, `src/silver/sql/listing.sql:17`), so a column
        appended in landing reaches both without a statement changing. Only a backfill may add
        a column (`src/silver/rebuild.py:281-290`, `src/silver/sink.py:112-113`). The ingest
        log records no contract (`src/silver/ingest.py:32-45`); v3 and v4 share their run ids.
- **Problem (review A4 and G, first bullet):**
    - The acquisition side lands v4 beside v3 on every run. v4 is v3 with four nullable columns
        appended to `listing_detail`: `photo_count` INTEGER, `address_visibility` VARCHAR
        (`hidden` or `exact`), `deposit_months` INTEGER, `is_seasonal_rental` BOOLEAN. A record
        extracted before the parser that reads them holds nulls; `extraction_version` tells the
        two apart. Its digest is `75532ee3…dd5f8`. v4 calls itself frozen once this repository
        pins it, so it is unfrozen today and nobody owns the move.
    - A v5 (v4 unchanged plus two photo tables) is planned there, landed beside v3 and v4.
    - Behaviour crosses the boundary by hand. The reader is tested only against landing this
        repository's synthetic writer wrote, and that writer takes version, digest and columns
        from the module the reader uses. No CI runs the reader against the real writer. The v1
        document exists in two wordings. The commit declares no writer version
        (`contracts/landing/v3/schema.json:13-56`).
- **Findings corrected:**
    - The stale rights on the landing v1 prefix are not in this repository: `infra/silver/`
        grants none, only the description of `lake_bucket` still names the prefix
        (`infra/silver/variables.tf:2`). The grant itself is a dependency below.
    - Only v1's document differs between the two sides; v3's and every schema are identical.
    - A path for a version move exists: the backfill (`docs/silver-rebuild.md:25`). Missing
        are a checklist and a refusal for a move that adds no column to Silver, v5's shape.
- **Constraints:** v3 and v4 never change. The fixture is synthetic. Every AWS step needs the
    owner's approval. No new resource and no new dependency.

## Scope

- **In scope:** `contracts/landing/v4/` copied and pinned, with a pin for every contract
    directory; one `LANDING_VERSION` in code and one `landing_prefix` in Terraform; the four
    columns in the synthetic generator, `listing_detail` and `listing`; an incremental run
    refused when Silver was built from another contract; a golden landing tree, a tag and the
    written move procedure; deployment, one backfill and its equality check.
- **Out of scope:**
    - any label or rule that uses the four fields, such as `address_visibility` in the location
        flags: each is its own feature;
    - a writer version in the commit: v4's commit fields are part of its frozen digest, so it
        belongs in v5, where Silver can record it;
    - landing v5 and its tables; a contract column in the ingest log; deleting v3 objects;
    - a rule for commits without rows (decided earlier: the acquisition side's gate).
- **Users / consumers:** Silver's readers, who gain four columns; the acquisition side's CI.

## Dependencies

- **Needs:** FEATURE-015 — task 2.1 adds one comparison to its guard and uses its
    `BackfillRequired` and refusal exit code. FEATURE-019 — task 4.3 deploys the way it defines.
- **Unblocks:** FEATURE-022 — it cuts the modules after the version left their names. A later
    move to v5 is the procedure of this plan, not a new design.
- **Acquisition side, after task 4.4 (not planned here):** stop landing v3 and drop its write
    right; remove the leftover grant on the landing v1 prefix; replace its contract copies with
    the tagged directories, which ends the v1 fork and makes its comments about the public pin
    true; add a CI job that checks out the tag and runs this repository's local backfill over
    its writer's output for the golden cases; land at most two versions at a time; declare a
    writer version in v5's commit.

## Open assumptions

- **Assumption:** landing v4 holds one commit per v3 commit, with the same run ids and row
    counts. — **Evidence:** the acquisition side's record of its v4 backfill; not checked from
    here, no AWS call was made. — **If wrong:** the comparison of 4.3 shows it before the
    consumer is enabled; the missing runs are landed there and the backfill is repeated.
- **Assumption:** no statement besides the two `*` selections is touched by the new columns. —
    **Evidence:** the other statements name their columns; unvalidated by a run. — **If
    wrong:** the backfill test of 1.3 fails and names the statement.

## Design

- **Primary approach:**
    - `src/contracts.py` gets `LANDING_VERSION` and two functions, `landing_columns(version)`
        and `landing_sha256(version)`, both defaulting to it. Reader, commit check, ingest log,
        snapshot summary and synthetic writer use them; `register_landing`, `write_landing` and
        `generate_landing` lose the version in their names. The move itself is then one constant
        plus the generator's new fields.
    - The pin test holds one digest per directory under `contracts/landing/` and fails on an
        unpinned one. That is the freeze: a version is published once it is pinned here.
    - The guard of FEATURE-015 also compares `landing_contract_sha256` in the current summary
        of `listing_detail` with the reader's digest. Today such a run replaces `walk` and
        `observation` and only then fails on `listing_detail`'s columns; with equal columns it
        would not fail at all, and the ingest log would call every run of the new version done.
    - `contracts/landing/v4/golden/` is a landing tree of three runs: a live run with one
        record that has the four values and one older record with nulls, a re-extraction replay
        of the older record, and a run without rows. `expected.json` beside it holds Silver's
        row counts and the replayed listing's four values after a backfill. The tree is written
        once by the synthetic writer from hand-written rows and committed, so the reader is
        tested against bytes that a joint change of writer and reader cannot move.
    - The tag `landing-contract-v4` names the commit at which the directory is the contract.
        The consumer test needs no new command: at the tag, the documented local backfill into a
        SQLite catalog runs every check the reader makes and exits non-zero on a refusal.
- **Contracts and invariants:**
    - `listing_detail` and `listing` gain the four columns at their landing position, nullable;
        no other table changes and no existing value changes.
    - `transformation_version` stays `silver-v1`: no rule changes, the input does, as with a new
        reference vintage (ADR 0004). The contract digest in each snapshot records the move.
    - A backfill is required, as the only path that adds a column; it is task 4.3. With the
        guard, every later version move is a backfill too, whatever its columns do.
- **The move as a procedure,** written into `contracts/landing/README.md` and the runbook: pin
    the new directory and its golden tree; set `LANDING_VERSION` and extend the generator; set
    `landing_prefix`; apply with `consumer_enabled = false`, backfill, compare, enable; tag; the
    acquisition side stops the previous version.
- **Not overtaken by v5:** v5 contains v4 unchanged, so this move is on the way to it and
    nothing here is redone. If v5 is published first, Silver still moves to v4; the feature
    that reads the photo tables moves it to v5 by the same steps.
- **OOP / SOLID:** pure functions and one constant are sufficient. **Patterns:** none.
- **Deliberately rejected complexity:** reading two versions behind a flag; a fixture written
    by the real writer (a binary file from the private side in a public tree); a
    `landing_version` Terraform variable, which could disagree with the image where a local
    pinned by a test cannot; digests for the documents, whose status lines must stay correctable.

## Approach

### Phase 1 — The contract and one version value

- [ ] **1.1 Pin v4.** RED: `tests/test_landing_contract.py` expects v4's digest, v4's tables
    equal to v3's except `listing_detail` with the four columns appended in order, and a pin
    for every directory under `contracts/landing/`. It fails because the directory is missing
    and v2 has no pin. GREEN: `schema.json` copied byte for byte, its document with the status
    line `frozen`, the pins.
- [ ] **1.2 One version value (refactor).** `LANDING_VERSION = "v3"`, the two functions, the
    three renames, `tests/test_silver_landing_v3.py` renamed to `test_silver_landing_runs.py`.
    Validation: the suite is green with no assertion changed.
- [ ] **1.3 The move.** RED: the synthetic writer's detail file has the four columns and its
    commit says v4; `parse_commit` refuses a v3 commit; after a backfill of the generator's
    landing, `listing_detail` and `listing` carry the four columns, null for a record of an
    older extraction version, and a re-extraction replay's values replace the nulls; stepwise
    commits give the same rows. All fail because the constant says v3. GREEN: the constant,
    the generator's four fields and one re-extraction scenario.

### Phase 2 — A version move is a backfill

- [ ] **2.1 Refusal before the first write.** RED: over tables whose `listing_detail` summary
    names another contract digest, an incremental run with a pending commit raises
    `BackfillRequired`, leaves every snapshot id unchanged, and `main` returns the refusal
    code; a backfill over the same tables succeeds and adds the columns. It fails today
    because `walk` is replaced before anything refuses. GREEN: the comparison in the guard.

### Phase 3 — The golden tree and the single source

- [ ] **3.1 Golden tree.** RED: `tests/test_landing_golden.py` reads the committed tree: every
    commit parses, a backfill into a temporary SQLite catalog gives `expected.json`, and the
    tree's rows and commits equal what `golden_landing_rows()` writes today. It fails because
    the tree does not exist. GREEN: the function, the tree, the expectations.
- [ ] **3.2 The contract's index.** `contracts/landing/README.md`: each version's state, the
    rule that this directory at its tag is the contract, the consumer test's command, the move
    procedure. v3's status line becomes `frozen, retired`. Validation: the leak guard.

### Phase 4 — Infrastructure and operation

- [ ] **4.1 One prefix in Terraform.** RED: `tests/test_infra_landing_prefix.py` reads
    `infra/silver/*.tf` as text, the way `tests/test_tooling_pins.py` reads the workflow, and
    expects exactly one `landing/v<n>`, equal to `landing/` plus `LANDING_VERSION`. It fails on
    four v3 prefixes and the v1 in a description. GREEN: the local and its four uses; statement
    ids, policy name and descriptions without a version. Validation: `terraform validate`.
- [ ] **4.2 Documentation.** `docs/silver-rebuild.md` (the four columns, v4 in every current
    command, a section "Moving to a new landing version"), `CLAUDE.md`,
    `docs/architecture.html`. Validation: leak guard and workflow validator.
- [ ] **4.3 Deployment and backfill (operator, with approval; at most $0.10).** Record every
    table's row count. Apply the new image and prefix with `consumer_enabled = false`, so no
    apply re-enables a mapping that was disabled by hand. Run one backfill task. Compare, as
    counts only: every table's rows with the counts before, the ingest log with v4's commits,
    the non-null values per new column, and a local backfill of the same landing. Enable the
    consumer and apply. Record the counts, the duration and the first incremental run in
    `dev/plans/implementations/FEATURE-021-landing-v4.md`.
- [ ] **4.4 Tag (operator, with approval; $0).** After the first incremental run from a v4
    commit, tag the merge commit `landing-contract-v4`, push the tag, and hand the dependency
    list to the acquisition side.

## Files

- **Create:** `contracts/landing/v4/` (`schema.json`, `README.md`, `golden/`),
    `contracts/landing/README.md`.
- **Change:** `src/contracts.py`, `src/silver/{commits,landing,ingest,rebuild}.py`,
    `src/synthetic/landing.py`, `infra/silver/{ecs,trigger,main,variables}.tf`,
    `contracts/landing/v3/README.md`, `docs/silver-rebuild.md`, `docs/architecture.html`,
    `CLAUDE.md`.
- **Tests:** `tests/test_landing_golden.py`, `tests/test_infra_landing_prefix.py` (new);
    `tests/test_landing_contract.py`, `tests/test_synthetic_landing.py`,
    `tests/test_silver_commits.py`, `tests/test_silver_incremental.py`,
    `tests/test_silver_cli.py`; the mechanical renames in the other Silver tests.

## Test strategy

- **Unit / contract:** the pins; v4's shape against v3; a commit of another version refused;
    the golden tree; generator rows with values, with nulls, replayed.
- **Integration / evolution:** backfill and stepwise commits into a local SQLite catalog give
    the same rows; the refusal writes nothing; a backfill over older tables adds the columns.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`,
    `terraform -chdir=infra/silver fmt -check` and `validate`.

## Operational and compliance impact

- **Data sourcing / legal:** four more fields cross the contract; all are facts of a listing.
- **Privacy / security:** no free text and no person: a count, a category, a number of months
    and a flag. `address_visibility` says how exact the stored coordinates are and stays in
    private Silver. The task role reads v4 instead of v3.
- **Observability:** the refusal reaches the log and the existing failure mail.
- **Failure and recovery:** a failed backfill is repeated; it is idempotent. There is no way
    back to v3 short of dropping the columns by hand, because the previous reader refuses
    tables with more columns. v3 stays landed until 4.4, as a source to compare with.
- **Migration / compatibility:** additive for readers; the trigger follows v4's commits.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Fargate and S3 requests, the backfill | about $0.04 per hour (1 vCPU, 2 GB, ARM64); $0.0004 per 1,000 GET, $0.005 per 1,000 PUT | one run of about 5 minutes, about 2,000 reads, some hundred writes | <$0.02, once |
| S3 storage | about $0.024 per GB-month | one more retained copy of Silver, tens of MB | <$0.01 |
| Scheduled runs | as today | same commits, same runs | $0 |
| **Total (new AWS components)** | | | **~$0/month, under $0.05 once** |

- **Cost drivers & cheaper alternatives:** nothing recurring changes. Staying on v3 costs $0
    and no work, but keeps three versions landed and the four fields unread. Waiting for v5
    saves one backfill of a cent and puts the fields behind a feature that is not built.
- **External / non-AWS costs:** none here; this repository's CI is free. The consumer test
    uses CI minutes of the acquisition side.
- **Budget check:** yes.

## Success criteria

- [ ] `listing_detail` and `listing` hold the four columns for the whole history, and every
    table's row count equals the count before the move.
- [ ] No code, test or Terraform file of this repository reads landing v3.
- [ ] An incremental run over tables of another contract writes nothing and is refused.
- [ ] The golden tree is read in CI, the tag exists, and the move procedure is written down.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak, workflow and Terraform checks pass
- [ ] The contract, the runbook and the cost are documented where affected
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner, at review):** does `transformation_version` stay `silver-v1`?
    Recommended: yes; no rule changed, and the contract digest per snapshot records the move.
- **Question (owner, at review):** move to v4 now, or wait and move once to v5? Recommended:
    now; v5 is planned, not built, and contains v4.
- **Question (owner, at review):** commit the golden tree as about fifteen small binary files,
    or generate it at the tag? Recommended: commit it; only fixed bytes test the reader
    independently of this repository's writer.
- **Question (owner, in 4.3 and 4.4):** approve the deployment with its backfill, then the tag.
- **Risk:** v5's photo runs add a commit without rows to v4, and each starts a Silver run. —
    *Mitigation:* well under a cent each; named to the acquisition side for its v5 plan.
- **Risk:** the tag is moved later. — *Mitigation:* the acquisition side records the tag's
    commit; a ruleset protecting `landing-contract-*` tags is free on a public repository.

## Progress log

- **2026-11-22** — Plan created from the architecture review of 2026-11-22 (A4, G first
    bullet). Verified in the code; three findings corrected in Context.
