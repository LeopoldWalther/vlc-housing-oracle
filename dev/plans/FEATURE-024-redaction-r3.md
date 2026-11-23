# FEATURE-024 — Redaction r3, a residue count per run and the privacy record

**Status:** 🔵 Planned · **Effort:** L (~14h) · **Priority:** Medium
**Branch root:** `feature/redaction-r3` · **Created:** 2026-11-22 · **Updated:** 2026-11-23

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-024.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-024-technical-plan.yaml`.

## Objective

Redaction `r3` removes the contact details and names that `r2` leaves in place, every Silver run
counts the texts a wider net still catches, and those texts are never sent to the model provider.
The owner's decisions on legal basis, processing agreement and retention are written into the
ADRs. What an erasure would have to touch is settled here; building it is proposed as a follow-up.

## Context

Source: the architecture review of 2026-11-22, section D (both bullets) and section C, fifth
bullet. Every claim below was checked against the code on that day.

- **Current state:**
    - `r2` is two replacements in one macro (`src/silver/sql/redact.sql:6-19`): a Spanish
        nine-digit number with at most one separator character between digits, and one of six
        honorific spellings followed by exactly one word. Only `description` passes through it
        (`src/silver/sql/listing_text.sql:9-12`, `tests/test_silver_redaction.py:95-98`).
    - Free text in Silver is the four columns of `listing_text`, plus the feature wordings
        `feature_pattern` derives from `features` (`src/silver/sql/feature_pattern.sql:12-18`).
        The enrichment store holds no text and no listing identifier: a hash, versions, two
        labels, a time and token counts (`src/enrichment/text_labels.py:32-54`).
    - The residue was measured twice by a hand-run query pinned to one snapshot
        (`analysis/silver-quality/06_redaction.sql:1-18`). A run reports print lines and two
        metrics (`src/silver/rebuild.py:875-919`); none concerns redaction.
    - A stored answer is keyed by the SHA-256 of the redacted description and the label version
        (`src/enrichment/sql/pending_text.sql:15`, `src/silver/sql/listing_text_label.sql:24`).
- **Problem:**
    - **D1, confirmed** by running `r2` on invented strings: `SRA. GARCÍA`, `D. José`,
        `600 - 000 - 000`, an e-mail address and the surname of `Sra. María García` survive, and
        `+44 7000 000000` keeps its prefix and last digit.
    - **D1, qualified.** Both scans found zero matches in title, location and features and zero
        e-mail addresses (`dev/plans/implementations/FEATURE-006-redaction-r2.md:43-55`).
        Unmeasured is what the query has no pattern for, and every run since.
    - **A premise corrected.** A new redaction does not change every hash: the version is not
        part of the key, so only a text whose redacted description changes is labelled again.
    - **D2, confirmed for every layer that holds text.** Snapshots are never expired
        (`docs/silver-rebuild.md:854-855`); landing text is verbatim and written once
        (`contracts/landing/v3/schema.json:398`). The two bounds that exist cover no table: 30
        days for the log group (`infra/silver/ecs.tf:46-49`), 29 for a batch at the provider.
    - **C5, confirmed.** ADR 0005 records that the text leaves the account and may leave the EU
        (`docs/adr/0005-model-derived-labels.md:40-45`). No legal basis, processing agreement or
        retention setting is named in `docs/adr/`, `src/` or `infra/`.
- **What erasing one listing would have to touch** (settled from the code, not built here):

| Layer | Holds of one listing | An erasure needs |
| --- | --- | --- |
| Raw pages, Bronze, landing | verbatim text and identifiers, in every run that saw it | a rewrite of write-once objects by the acquisition repository; this repository only reads landing |
| Silver, current tables | rows in 11 of 14 tables, keyed by `source_listing_id` | a backfill after landing changed; no table is edited by hand |
| Silver, older snapshots and files | the same rows as every earlier run wrote them | FEATURE-020's expiry of every older manifest, pinned ones too, and its file removal |
| Enrichment store | a hash of the redacted description with two labels; one hash can serve several listings | the owner's decision (Q6); the labeller's role cannot delete (`infra/silver/labels.tf:60-64`) |
| Model provider | the redacted description, 29 days in a batch (`docs/silver-rebuild.md:573-580`) | no code deletes a batch today |
| Outside the lake | the private evaluation set of 230 texts, operator copies, query results | the operator, by hand |

- **Constraints:** DuckDB's RE2 has no look-around. Tests use invented names, numbers and
    addresses only. This plan states no legal conclusion: it records what the owner decides.

## Scope

- **In scope:** the `r3` replacements; contact details redacted in all four text columns; residue
    classes per row, counted per run and withheld from the labeller; the runbook and public pages;
    an amendment to ADR 0005 and a new ADR 0006 with the owner's answers; one backfill.
- **Out of scope:** an erasure command, lifecycle rules and any change to landing (see Open
    questions); names without an honorific or a contact cue, which no pattern finds; web
    addresses, which the quality report reads as agencies; a prompt edit or a new `label_version`;
    another provider or region; deleting batches; a metric and an alarm, unless Phase 5 is chosen.
- **Users / consumers:** every reader of `listing_text`, the labeller, the operator.

## Dependencies

- **Needs:** FEATURE-015 — its guard refuses an incremental run between the deployment of `r3`
    and the backfill. FEATURE-017 — the labeller refuses text of another redaction version, and
    task 2.3 edits the read it adds. FEATURE-016 — its JSON line carries the count of task 2.2.
    FEATURE-020 — not for the tasks here, but `r2` text leaves the lake only with its first
    expiry, and the erasure follow-up cannot start without it.
- **Unblocks:** the erasure follow-up; any Gold feature or model that uses description text.

## Open assumptions

- **Assumption:** the candidate patterns do what the table below says. — **Evidence:** a
    throwaway DuckDB check on 38 invented strings: `r2`'s ten redactions and six survivors are
    unchanged, fifteen cases `r2` misses or cuts short are redacted, and seven-digit prices,
    `llamar a cualquier hora` and `contactar con nosotros` survive. It proves nothing about real
    text. — **If wrong:** task 2.4 shows it in counts per class before anything is merged.
- **Assumption:** `r3` changes few descriptions and its residue classes flag well under 1 % of
    texts. — **Evidence:** `r2` placed a placeholder in at most 689 of 20,508 and left 13 matches
    of the nearest classes; unvalidated for `r3`. — **If wrong:** the cost is bounded by all sale
    texts; a noisy class is narrowed in 2.4, before `r3` has written a row. Later it is `r4`.
- **Assumption:** the sink adds the new column to the existing table
    (`src/silver/sink.py:140-146`). — **If wrong:** the test of 2.1 fails and names the gap.

## Design

- **Primary approach:** widen the macro, flag what stays doubtful, keep flagged text inside.

| Rule | Applies to | Result | Invented example |
| --- | --- | --- | --- |
| E-mail address | all four columns | `[email]` | `nombre@example.com` |
| Number with `+` or `00` and a country code | all four | `[phone]` | `+44 7000 000000` |
| Spanish number, up to three separator characters between digits | all four | `[phone]` | `600 - 000 - 000` |
| Honorific, also in capitals or as `D.`, with up to three name words and their particles | description | `[name]` | `SRA. GARCÍA`, `Sra. María García López` |
| Contact cue with up to three capitalised words | description | cue kept, `[name]` | `Preguntar por Ana` |
| Residue `digit_run`: nine or more digits left | all four | flag | `Ref 123456789` |
| Residue `at_sign`: `@` or `arroba` left | all four | flag | `nombre arroba example` |
| Residue `honorific`: an honorific left, any case | description | flag | `Sra. de` |

- **Boundaries and data flow:**
    - `redact.sql` holds three macros: contact details, contact details plus names, and the
        residue classes of a text. `listing_text.sql` applies them and writes one new column,
        `redaction_residue`, a list of class names that is empty for a clean row.
    - The run counts flagged rows per class over the table as it stands after its writes, from
        the read it already makes (`src/silver/rebuild.py:515`), prints one line and puts the
        total into FEATURE-016's JSON line. No person reads a text, and the count of any past
        run is also readable from that run's snapshot.
    - The labeller reads the column and leaves a flagged text out of the pending set. Silver's
        join needs no change: a text never sent has no answer. No rule is copied between the two.
- **Contracts and invariants:**
    - **`redaction_version` becomes `r3` and history is replaced by one explicit backfill.
        `transformation_version` stays:** the redaction has its own version for this case, and
        the new column is written by the redaction rule. `r3` names the replacements and the
        residue classes together, so a change to either is `r4`.
    - A flagged text never leaves the account. A label stored for an unchanged text stays.
    - The prompt is not edited (`src/enrichment/prompt.py:35` names two placeholders): an edit
        is a new label version and labels every text again. `[email]` joins the next version.
- **OOP / SOLID, patterns:** SQL macros, one column, one predicate. None.
- **Deliberately rejected complexity:**
    - a metric filter on the log line: its metric is priced like a published one, so the log
        gives the measurement for nothing but never the mail;
    - a non-zero exit code or a mail sent by the run itself: an exit code that lies about
        success, or a third alerting path;
    - the residue rule repeated in the labeller; names redacted in titles and locations, which
        hold street names (`calle Don Juan`).

## Approach

### Phase 1 — The r3 replacements

- [ ] **1.1 Contact details in every text column.** RED in `tests/test_silver_redaction.py`:
    `+44 7000 000000`, `0033 6 00 00 00 00`, `+1 (555) 000-0000` and `600 - 000 - 000` give
    `[phone]`; `escriba a nombre@example.com hoy` gives `escriba a [email] hoy`; a title, a
    location and a feature holding `600 000 000` come back with `[phone]`; `precio 1.250.000 €`
    and `1250000 euros` survive; the version test expects `r3`. All but the survivors fail today.
    GREEN: the contact macro on four columns, `REDACTION_VERSION = "r3"`, the pins of `r2`.
- [ ] **1.2 Names.** RED: `SRA. GARCÍA`, `DON JUAN PÉREZ`, `D. José` and `Sr. Pérez de la
    Fuente` give `[name]`; `Sra. María García López vende` gives `[name] vende`; `Preguntar por
    Ana` gives `Preguntar por [name]`; `llamar a cualquier hora`, `contactar con nosotros`,
    `Sra. de` and `Sr. y` survive; the title `Piso en calle Don Juan` stays. The first six fail
    today. GREEN: the name macro. REFACTOR: the macro's header comment says what `r3` adds.

### Phase 2 — The residue, counted per run and kept inside

- [ ] **2.1 The column.** RED: rows built from `Ref 123456789`, `nombre arroba example punto
    com`, `Sra. de` and `Piso luminoso` carry `['digit_run']`, `['at_sign']`, `['honorific']`
    and `[]`; a title holding a nine-digit run flags its row; a backfill over a catalog whose
    `listing_text` predates the column succeeds, and incremental runs equal a backfill. Fails:
    the column does not exist. GREEN: the residue macro and the column.
- [ ] **2.2 The count.** RED in `tests/test_silver_cli.py`: a run over two flagged and one clean
    text prints `redaction residue: 2 texts (at_sign 0, digit_run 1, honorific 1)` and its JSON
    line holds `"redaction_residue": 2`; a run with nothing pending prints neither. Fails: no
    such line or key. GREEN: counts on `ProcessResult`, one line in `_report`, one key.
- [ ] **2.3 The labeller withholds.** RED in `tests/test_enrichment_text_labels.py`: a flagged
    sale text is not pending and the report prints `withheld texts: 1`; an unflagged twin is
    pending. Fails: both are pending. GREEN: the column in the read, one predicate in
    `pending_text.sql`, one report line.
- [ ] **2.4 Rehearsal on a private copy (operator, before the merge; at most $0.01).** Backfill
    a fresh local copy of landing with the branch, as the runbook's equality check does. Record
    counts only: flagged texts per class, and sale descriptions whose hash differs from current
    Silver. A class above 1 % of texts, or more than 2,000 changed texts, goes back to Phase 1.

### Phase 3 — The record

- [ ] **3.1 Runbook and public pages.** `docs/silver-rebuild.md`: the `listing_text` row, the
    new line, the withheld count, a query for the count over time, how to read flagged rows
    privately. `README.md` and `docs/architecture.html` say what is true. Validation:
    `python dev/tools/check_leaks.py` and `python dev/tools/validate_workflow.py`.
- [ ] **3.2 ADR 0005, amended.** One dated amendment records the owner's answers to Q1 to Q5 in
    the owner's words, each with the document it rests on; an unanswered question is written as
    open, with a date. Validation: five answers or open marks; the leak guard.
- [ ] **3.3 ADR 0006, new: where listing text is kept and for how long.** The table of Context
    with the owner's retention per layer (Q7), the answer to Q6, and the sentence that no
    erasure path exists yet. The runbook links to it.

### Phase 4 — History

- [ ] **4.1 Deploy and backfill (operator, needs the owner's approval; at most $0.10).** As
    FEATURE-006 task 2.1: the merge commit's image, `terraform apply`, the backfill with the
    trigger disabled. Validation: exit 0, every row records `r3`, counts equal the rehearsal's.
- [ ] **4.2 Labelling the changed texts (paid, needs the owner's approval; at most $2.00 of the
    plan's API credit, nothing billed while the credit lasts).** Changed texts are pending by
    themselves and go out under the per-run cap. Record texts sent, texts withheld and tokens in
    `dev/plans/implementations/FEATURE-024-redaction-r3.md`; counts only.

### Phase 5 — An alarm, only if the owner decides for it after four weeks of counts

- [ ] **5.1 Metric and alarm.** RED: the fake CloudWatch client of `tests/test_silver_cli.py`
    receives `RedactionResidue` after a run and not after an idle one; `tests/test_infra_silver.py`
    (FEATURE-016's file) finds an alarm on it above `var.redaction_residue_accepted` with missing
    data ignored. GREEN: one entry in `_publish_metrics`, one alarm block, one variable.
- [ ] **5.2 Apply (operator, needs approval; at most $0.40 a month).** Set the accepted count to
    the level the four weeks showed, plan, apply, and add the alarm to the runbook's table.

## Files

- **Change:** `src/silver/sql/redact.sql`, `src/silver/sql/listing_text.sql`,
    `src/silver/rebuild.py` (version, counts, report), `src/silver/telemetry.py` (one key),
    `src/enrichment/sql/pending_text.sql`, `src/enrichment/text_labels.py` (read, report),
    `docs/silver-rebuild.md`, `docs/adr/0005-model-derived-labels.md`, `README.md`,
    `docs/architecture.html`. Phase 5 only: `infra/silver/alerts.tf`, `infra/silver/variables.tf`.
- **Create:** `docs/adr/0006-listing-text-retention.md` and the implementation note.
- **Tests:** `tests/test_silver_redaction.py`, `tests/test_silver_cli.py`,
    `tests/test_enrichment_text_labels.py`, and the suites that pin `r2`.

## Test strategy

- **Unit:** each rule on invented strings, in every column it applies to, with the survivors
    that prove it does not eat prices, references, years or ordinary words after a cue.
- **Contract / fixtures:** the synthetic landing only; numbers use repeated digits, addresses
    the `example` domains, names two common surnames. No real text enters the repository.
- **Integration, idempotency, evolution:** backfill and stepwise commits on a local SQLite
    catalog give the same rows; a table that predates the column gains it; the labeller and
    then Silver, with faked batches.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`, `ruff check` and
    `ruff format --check`, the leak guard, the workflow validator; Phase 5 adds `terraform
    fmt -check` and `validate` under `infra/silver/`. **Manual:** the counts of 2.4, 4.1, 4.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract. Tasks 3.2 and 3.3 record the
    owner's decisions; the plan and the Implementer add no legal judgement of their own.
- **Privacy / security:** less personal data in current Silver and in what is sent. `r2` text
    stays in older snapshots until FEATURE-020's first expiry, and landing stays verbatim. The
    count line holds numbers only; flagged rows are read in the private lake, never printed.
- **Observability:** one count per run, in the log and in the table's snapshots.
- **Failure and recovery:** a failed backfill leaves the previous snapshots; rerun it under the
    same image. A flagged text stays unlabelled until a later redaction version clears it.
- **Migration / compatibility:** one added column. Labels of changed texts are null for about
    two runs after the backfill.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. |
|---|---|---|---|
| Column, count line, labeller predicate | none | a few bytes per row and run | $0.00/month |
| Backfill, once | Fargate minutes | about three minutes | under $0.10 once |
| Labelling changed texts, once | about $0.11 per 1,000 texts in a batch | expected a few hundred; at most all 17,236 sale texts | under $0.10, at most $1.90 once |
| Metric and alarm, Phase 5 only | $0.30 and $0.10 a month beyond the free ten | not built unless decided | $0.00, else at most $0.40/month |
| **Total (new AWS components)** | | | **$0.00/month; at most $2.00 once** |

- **Cost drivers & cheaper alternatives:** the only recurring price is the optional alarm; the
    design without it is the cheapest that still measures every run. The one-off labelling
    would fall to $0 if the backfill ran before the labeller's backlog is sent; the owner chose
    not to hold the labeller's deployment back for that.
- **External / non-AWS costs:** the model API, from the plan's credit, as in the table.
    **Budget check:** yes.

## Success criteria

- [ ] Every row of `listing_text` records `r3`, and each case of Phase 1 is redacted or survives.
- [ ] Every run that processed something reports its flagged texts per class, without a text.
- [ ] No flagged text is submitted to the model provider.
- [ ] ADR 0005 and ADR 0006 hold the owner's answer, or an open mark with a date, for Q1 to Q7.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, infrastructure, and workflow checks pass
- [ ] Data, operational, migration, and cost contracts are documented where affected
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner, at review): split off the erasure path?** The three parts do not fit one
    feature. Redaction and the record fit here. An erasure needs FEATURE-020's file-removing
    expiry, a counterpart in the acquisition repository that rewrites write-once objects, and an
    amended ADR 0003. *Recommendation:* yes, as its own plan after FEATURE-020, Q6 and Q7.
- **Question (owner): an alarm on the count?** *Recommendation:* not yet. A threshold cannot be
    chosen before the classes' base count is known, the labeller already withholds flagged text,
    and the price is $0.40 a month on a platform under $1. Decide after four weeks of counts.
- **Decided (owner, 2026-11-23): FEATURE-012's deployment does not wait for this backfill.**
    The labeller's backlog is sent as `r2` text, and task 4.2 labels the texts that `r3`
    changes again, for at most $1.90 once.
- **Questions the owner must answer for tasks 3.2 and 3.3** (not legal advice; *recommendation*
    for all: answer from the provider's current documents, and take advice where unsure):
    - **Q1.** On which legal basis is listing text that may hold personal data collected,
        stored and sent to the model provider, and where is that assessment kept?
    - **Q2.** Is a data-processing agreement with the provider in force for the organization
        that holds the API key (document and date)? **Q3.** Which transfer mechanism covers
        processing outside the EU?
    - **Q4.** Which retention applies at the provider for this organization, and should ended
        batches be deleted after collection instead of staying readable for 29 days?
    - **Q5.** Do the answers cover the 230 evaluation texts already sent as direct requests?
    - **Q6.** Is a stored hash with its two labels erased with its listing, or kept?
    - **Q7.** How long are raw pages, landing, Silver snapshots, stored answers and operator
        copies kept, and are erasure requests for single listings served, within what time?
- **Risk:** the wider patterns redact a harmless word or number list, or a noisy residue class
    leaves many texts unlabelled. — *Mitigation:* the rehearsal's counts and its 1 % bound before
    the merge; the withheld count in every labeller report.
- **Risk:** personal data that no pattern describes stays. — *Mitigation:* none; ADR 0006 says so.

## Progress log

- **2026-11-22** — Plan written from the architecture review of 2026-11-22 (D1, D2, C5). D1 and
    D2 qualified, the cost premise corrected; erasure proposed as a follow-up feature.
- **2026-11-23** — Owner decided that FEATURE-012 task 4.3 does not wait for `r3`; relabelling
    the changed texts once is accepted.
