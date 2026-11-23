# FEATURE-022 — One declaration per Silver table, and `rebuild.py` cut into modules

**Status:** 🔵 Planned · **Effort:** L (~18h) · **Priority:** Medium
**Branch root:** `feature/table-registry` · **Created:** 2026-11-22 · **Updated:** 2026-11-22

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-022.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-022-technical-plan.yaml`.

## Objective

A Silver table is declared once, in a registry that the run, its report, the tests and a check of
the documentation read. The 923-line module becomes modules with one responsibility each, no rule
leaves a relation behind in the DuckDB session, and names say what the code does today. This is a
refactor: every table keeps every row and every column, proven by a digest per table.

## Context

- **Current state**, checked in the code on 2026-11-22 (`rebuild.py` is `src/silver/rebuild.py`):
    - `rebuild.py` holds the run (`:210`), seven rule wrappers (`:598-729`), the reader of
        census vintages (`:464`), the S3 listing of commits (`:759`), the command (`:778`) and
        the metrics (`:899`).
    - Which tables exist is held in six constants (`rebuild.py:47`, `:88`, `:101`, `:145`,
        `src/silver/property_group.py:15`, `src/silver/ingest.py:20`), a hand-written list in
        `_report` (`:881`) and three documents. The write strategy is control flow
        (`:267-316`); the only declared key is `LISTING_KEY` (`:141`).
    - `listing_text_label` (FEATURE-012) was wired in at ten places of `rebuild.py` (`:101`,
        `:104`, `:219`, `:247`, `:302`, `:309`, `:496-549`, `:694`, `:807-857`, `:894`) and in
        seven other files: its SQL, `infra/silver/ecs.tf:198`, the runbook, `README.md:92`,
        `docs/architecture.html:750` and two shared test modules. "Five or more" understates it.
    - Five rules replace the session's `silver_<table>` with their narrowed input and state a
        call order in their docstrings (`:604`, `:621`, `:636`, `:650`, `:666`); three already
        use private names (`:673-729`). `_compute_listings` leaves three relations registered
        and re-points four (`:424-459`). `_silver_columns` reads `silver_<name>` by name when
        the catalog lacks the table (`:591`); two reviews corrected a plan for exactly that
        (`dev/reviews/REVIEW-FEATURE-009.md:90`, `dev/reviews/REVIEW-FEATURE-013.md:38`).
    - `build_tables` computes ten statements (`:356`); the run uses the three day tables
        (`:267`). The other seven are that typed fallback and the oracle of eight test modules.
    - Silver and the labeller each write the eligibility rule and the key
        (`src/enrichment/sql/pending_text.sql:10`, `src/silver/sql/listing_text_label.sql:16`),
        the namespace (`rebuild.py:42`, `src/enrichment/text_labels.py:261`) and the S3 set-up
        (`:867`, `src/enrichment/text_labels.py:443`).
    - Tests build the SQLite catalog 16 times in ten modules and patch `load_catalog` in three
        (`tests/test_silver_cli.py:91`, for one), because `main` takes no catalog.
- **Findings changed by the check:**
    - A registry does not end the double computation; that needs typed empty tables from
        somewhere else than a second computation. This plan removes the trap and leaves the cut
        to the follow-up of the memory finding, which waits for FEATURE-016's numbers. In an
        incremental run the seven statements cover only the runs of the touched days.
    - No metric and no alarm carries an old name (`infra/silver/alerts.tf:118-138`).
    - The duplicated S3 set-up is real and stays: three statements twice, with no home but a
        utility module. The rest of the labeller duplication is low risk, because reader and
        label version are shared already; it is still removed.
- **Constraints:** no rule writes anything different, so `transformation_version` stays as it
    is and no backfill runs. No new dependency.

## Scope

- **In scope:** the registry; the module cut; rule-private session names; the namespace and the
    label key held once; shared test helpers and a catalog parameter for both commands; the
    renames of the table under Design.
- **Out of scope:** computing only what a run reads; a declared Arrow schema or a published
    Silver contract per table; the grain of the tables that are not upserted, which stays
    documentation; an object for the run's two optional inputs; splitting
    `src/enrichment/text_labels.py`; typed SDK clients; faster tests; FEATURE-018 and FEATURE-021.
- **Users / consumers:** whoever adds the next table or rule; the operator, through the command's
    name and the runbook's path.

## Dependencies

- **Needs:** FEATURE-018 first: it rewrites `src/silver/feature_patterns.py`, its tests and the
    runbook's example; this feature leaves that module in place and renames the runbook once.
    FEATURE-021 first: it changes the landing version in the reader, the commits and the
    command, and carries a backfill; the digests of task 1.1 need final rules, and moving the
    code under a plan in review would point that plan at paths that no longer exist.
    FEATURE-015, 016, 017, 019 and 020 edit the same module or define the deployment and are
    merged before phase 2 starts. Phase 1 moves no code and may land earlier.
- **Unblocks:** FEATURE-023 and FEATURE-024 are written against the new modules; the follow-up
    of the memory finding becomes a change in one function.

## Open assumptions

- **Assumption:** the digest of a table's sorted rows is equal on every machine. — **Evidence:**
    the stepwise-equals-backfill tests compare the same representation within one process;
    across processes it is unvalidated. — **If wrong:** task 1.1 excludes the unstable column by
    name, with the reason in a comment.
- **Assumption:** nothing outside the repository starts the Silver command by its module name. —
    **Evidence:** the task definition passes arguments only (`infra/silver/ecs.tf:192`) and the
    entry point is in the image (`Dockerfile:35`). — **If wrong:** the owner says so at review
    and the name stays.

## Design

- **Primary approach:** `src/silver/tables.py` holds `NAMESPACE` and one frozen value object per
    table, in the order of the report:

    | Field | Meaning | Read by |
    | --- | --- | --- |
    | `name` | the table's name | run, report, tests, documentation check |
    | `write` | `days`, `upsert` or `whole` | the run's day loop and upsert pair, the report's line format |
    | `key` | key columns, set only for `upsert` | `upsert_rows` |
    | `needs` | the run input without which the table is not written, or none | report, tests |

    Thirteen entries: `walk`, `observation`, `listing_detail` (`days`); `listing`, `listing_text`
    (`upsert` on `source`, `source_listing_id`); eight `whole`, of which `listing_location` needs
    `census_sections` and `listing_text_label` needs `text_labels`. `landing_ingest` stays with
    its module: it is the checkpoint, has a declared schema and its own writer.
- **Boundaries and data flow:**

    | Today in `rebuild.py` | Goes to |
    | --- | --- |
    | `NAMESPACE`, table constants | `src/silver/tables.py` |
    | versions, thresholds, column tuples, rule wrappers, `build_tables` | `src/silver/rules.py`, next to `src/silver/sql/` |
    | `local_commits`, `s3_commits` | `src/silver/commits.py` |
    | `read_census_sections`, `ReferenceDataError` | `src/reference/census_sections.py`, next to the writer |
    | `process_landing` and its private steps | `src/silver/run.py` |
    | `main`, `_report`, metrics | `src/silver/cli.py` |

    `tables.py` imports nothing of the project, so the labeller imports the namespace from it
    without a cycle. `cli` imports `run`, `run` imports `rules`; no rule imports the run.
- **Contracts and invariants:**
    - A run's snapshot ids name exactly the registry's tables whose input was given.
    - No relation outlives the function that made it, except the landing views and what
        `_prepare` sets: variables, macros and the feature registry, all constants.
    - A rule receives its inputs as arguments and registers them under names of its own. The
        typed empty table for a table the catalog lacks is passed by value from the run's own
        result, never read from the session by name.
    - Whether a text is labelled and under which key is one SQL macro, owned by the enrichment
        package and loaded by the labeller's query and by Silver's rule.
- **OOP / SOLID:** one frozen dataclass without behaviour; everything else stays functions.
    Both commands accept a catalog as they accept their clients (dependency injection).
- **Patterns:** none.
- **Deliberately rejected complexity:** a registry that binds each table to a rule callable and
    its input columns, with a generic executor (the rules share reads on purpose and take
    different inputs); one module per rule (seven files of twenty lines); a module under the old
    name that forwards to the new command.
- **Names**, each a compatibility question (see Open questions):

    | Name | Held by | Recommendation |
    | --- | --- | --- |
    | `python -m src.silver.rebuild` | `Dockerfile:35`, `README.md:178`, runbook, 19 test modules; no Terraform | rename to `python -m src.silver.cli` |
    | `docs/silver-rebuild.md` | `README.md:76`, `docs/architecture.html:1264`, the data-quality report, the failure mail and a description in Terraform (`infra/silver/alerts.tf:97`, `main.tf:6`), 29 records under `dev/` | rename to `docs/silver-runbook.md` |
    | `rebuild_started_at` | the summary of every snapshot (`rebuild.py:338`), read by no code | keep; the runbook says what it means |
    | `labeler`, `TextLabeler` | Python only; prose and Terraform say `labeller` | rename the Python names |
    | `labeled_at` | stored answers and `listing_text_label` | keep: a stored column |
    | `src.enrichment.text_labels` | the labeller task's entry point (`infra/silver/labels.tf:115`) | keep |

## Approach

Every task leaves the whole suite green. A move is one commit that edits no moved line.

### Phase 1 — Characterisation and test support

- [ ] **1.1 A digest per table.** `tests/test_silver_characterisation.py` backfills the synthetic
    landing with a synthetic vintage and stored answers and asserts, per table, the column
    names, the Arrow types and a SHA-256 over the sorted rows, plus the sequence of report
    lines. The constants are recorded on the commit before this feature. In place of RED:
    change one threshold, see the digest of `listing_quality` fail, revert.
- [ ] **1.2 Test support.** `tests/support.py` gets the catalog factory and the run helper that
    `tests/test_silver_incremental.py` defines three times. Validation: the suite is green and
    `SqlCatalog(` occurs in `tests/support.py` and the child script of the sink test only.
- [ ] **1.3 A catalog parameter.** RED: the tests of both commands call
    `main([...], catalog=catalog)` and fail with a `TypeError`. GREEN: the keyword on both
    commands; the four `load_catalog` patches go.

### Phase 2 — The registry

- [ ] **2.1 The declaration.** RED: `tests/test_silver_tables.py` asserts the thirteen names in
    order, each strategy, a key on exactly the upserted tables, and that a run given both
    inputs returns snapshot ids for exactly these names; it fails on the missing module.
- [ ] **2.2 The run reads it.** Refactor under 1.1: the day loop, the upsert pair and `_report`
    iterate the registry; `DAY_TABLES`, `LOCATION_TABLE`, `TEXT_LABEL_TABLE`,
    `PROPERTY_GROUP_TABLE` and `LISTING_KEY` go; the tests enumerate from the registry.
- [ ] **2.3 The documents follow.** The same test module asserts that the runbook, `README.md`
    and `docs/architecture.html` each name every table of the registry. It passes today; in
    place of RED: remove one name from a document, see it fail, revert.

### Phase 3 — Rules without shared names

- [ ] **3.1 Move.** RED: the rule test modules import from `src.silver.rules` and fail on the
    missing module. GREEN: the move of the second row of the table above.
- [ ] **3.2 Private inputs.** RED, one rule per commit, modelled on
    `tests/test_silver_listing_location.py:204`: a marker table named `silver_<input>` is
    unchanged after the call and no relation of the rule remains; it fails because the rule
    replaces the table. GREEN: the rule's SQL reads `<rule>_<input>`, the wrapper registers and
    removes it, and `build_tables` calls the five rule functions with its own results.
    `SILVER_TABLES` goes.
- [ ] **3.3 Nothing outlives its function.** RED: after `process_landing` with both inputs the
    session holds only landing views and the feature registry; it fails on `silver_*`,
    `newest_*`, `chosen_detail` and `affected_*`. GREEN: `build_tables` and `_compute_listings`
    drop what they made, and `_silver_columns` takes its typed empty table as an argument.

### Phase 4 — The cut

- [ ] **4.1 Commit listing.** RED: the tests import `local_commits` and `s3_commits` from
    `src.silver.commits` and fail on the missing names. GREEN: the move.
- [ ] **4.2 Vintage reader.** RED: the tests import `read_census_sections` and
    `ReferenceDataError` from `src.reference.census_sections`. GREEN: the move; the six column
    names are stated once for writer and reader.
- [ ] **4.3 Run and command.** RED: `tests/test_silver_cli.py` starts
    `python -m src.silver.cli` and fails on the missing module. GREEN: `run.py`, `cli.py`,
    `rebuild.py` deleted, the image's entry point, the commands in `README.md` and the runbook.

### Phase 5 — Held once with the labeller

- [ ] **5.1 Namespace.** The labeller imports `NAMESPACE`. A test reads `infra/silver/main.tf`
    as `tests/test_tooling_pins.py` reads the workflow and asserts the Glue database's name
    equals it. Validation: the literal occurs once under `src`.
- [ ] **5.2 Label key.** RED: a test loads `src/enrichment/sql/text_label_key.sql` and expects
    the hash for a sale with text, and null for a rental, a null and a description of white
    space only; it fails on the missing file. GREEN: the macro, used by both statements.

### Phase 6 — Names and operation

- [ ] **6.1 One spelling.** RED: the tests import `src.enrichment.labeller` and `TextLabeller`.
    GREEN: the Python renames.
- [ ] **6.2 Runbook and prose.** Rename the runbook and rewrite its path in every file of the
    tree, the two Terraform texts included; correct the package docstring and
    `SilverSchemaError`'s; rename `tests/test_silver_rebuild.py` to
    `tests/test_silver_backfill.py`. Validation: `terraform validate`, the leak guard, the
    workflow validator, and no `silver-rebuild` left outside this plan.
- [ ] **6.3 Deployment and comparison (operator, each with the owner's approval).** Deploy the
    image of the merge commit the way FEATURE-019 defines: a new task-definition revision and
    the two texts changed in place, upper bound $0. Then sync landing once, backfill it locally
    into two SQLite catalogs, with the commit before task 1.1 and with the merge commit, and
    compare row count and digest per table, printing no row. Upper bound $0.50 of S3 requests
    and transfer, assuming landing is under 5 GB; the operator reads the size first.

## Files

- **Create:** `src/silver/tables.py`, `src/silver/rules.py`, `src/silver/run.py`,
    `src/silver/cli.py`, `src/enrichment/sql/text_label_key.sql`, `tests/support.py`,
    `tests/test_silver_tables.py`, `tests/test_silver_characterisation.py`.
- **Rename:** `src/enrichment/labeler.py`, `docs/silver-rebuild.md`,
    `tests/test_silver_rebuild.py`. **Delete:** `src/silver/rebuild.py`.
- **Change:** `src/silver/commits.py`, `src/silver/sink.py`, `src/silver/property_group.py`,
    `src/silver/__init__.py`, six statements under `src/silver/sql/`,
    `src/enrichment/sql/pending_text.sql`, `src/enrichment/text_labels.py`,
    `src/reference/census_sections.py`, `Dockerfile`, `infra/silver/main.tf`,
    `infra/silver/alerts.tf`, `README.md`, `docs/architecture.html`, the data-quality report.
- **Tests:** every module under `tests/` that imports `src.silver.rebuild` or builds a catalog.

## Test strategy

- **Unit:** the registry's content; the label key macro on its boundary values.
- **Contract / fixtures:** names, types and digest per table on the synthetic landing; the
    report's line sequence; the documents against the registry; Terraform's database name.
- **Integration and idempotency:** the existing tests of stepwise runs against a backfill, of
    repeated runs, of a catalog that predates a table and of the schema refusal, unchanged.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`,
    `terraform validate` in `infra/silver`.
- **Manual:** the comparison of 6.3.

## Operational and compliance impact

- **Data sourcing / legal, privacy / security:** nothing crosses the landing contract; no new
    data, no IAM change. The comparison prints counts and digests only.
- **Observability, failure and recovery:** the report's lines, the metrics and the alarms are
    unchanged. A wrong move fails the suite, not a run.
- **Migration / compatibility:** the command's module name and the runbook's path change if the
    owner agrees. The old image keeps running until 6.3; the new entry point arrives with the
    new image and needs no change of the task's arguments. No stored name changes.

## Estimated monthly cloud cost

$0/month — no resource added or changed beyond a task-definition revision and two texts.

- **Cost drivers & cheaper alternatives:** the run computes what it computes today. The one-time
    comparison of 6.3 costs at most $0.50; the cheaper alternative is to skip it and rely on the
    synthetic digests at $0.
- **External / non-AWS costs:** none. **Budget check:** yes.

## Success criteria

- [ ] The digests, names and types of task 1.1 are the same constants on the merge commit.
- [ ] No table name is a constant outside `src/silver/tables.py` and the ingest log's module.
- [ ] `src/silver/rebuild.py` is gone; command, run, rules, commit listing and vintage reader
    each live in the module named under Design.
- [ ] After a run the session holds only landing views and the feature registry.
- [ ] The namespace literal and the eligibility rule each occur once under `src`; no test
    patches `load_catalog`; the catalog factory exists once.
- [ ] The comparison of 6.3 shows no difference, or the owner chose to skip it.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak, Terraform and workflow checks pass
- [ ] The runbook, `README.md` and the architecture page name modules and command as they are
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner, before 4.3):** rename the command to `python -m src.silver.cli`?
    Recommended: yes, without a forwarding module; only the image and the documents name it.
- **Question (owner, before 6.2):** rename the runbook to `docs/silver-runbook.md` and rewrite
    the path in the 29 records under `dev/`? Recommended: yes, links only.
- **Question (owner):** keep `rebuild_started_at` and `labeled_at`, and spell the Python names
    `labeller` as prose and Terraform do? Recommended: yes to both. The two stored names live in
    snapshots and in immutable answer parts; a rename leaves two names in history.
- **Question (owner):** stop computing the seven unused statements in this feature?
    Recommended: no. It changes duration and memory while FEATURE-016 measures them.
- **Question (owner):** is the digest of task 1.1 the pin FEATURE-015 left open? Recommended:
    yes. It pins outputs, so it sees a rule change only where the synthetic landing exercises it.
- **Question (owner, in 6.3):** approve the deployment, and separately the comparison on real
    data or its omission.
- **Risk:** a wide move collides with a feature in flight. — *Mitigation:* phase 2 starts when no
    other branch edits `rebuild.py`; moves are commits without edits, so a rebase is mechanical.
- **Risk:** `_compute_listings` still re-registers landing for the chosen runs' text. —
    *Mitigation:* nothing reads landing after it; task 3.3 pins which relations remain.

## Progress log

- **2026-11-22** — Plan created from the architecture review of 2026-11-22, section F, items 1
    to 4, 6 and 7. Each claim was checked in the code; four were changed (see Context).
