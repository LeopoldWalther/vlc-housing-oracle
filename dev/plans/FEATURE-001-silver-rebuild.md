# FEATURE-001 — Silver on Iceberg against landing/v1

**Status:** 🔵 Planned · **Effort:** L (~16-20h) · **Priority:** High
**Branch root:** `feature/silver-rebuild` · **Created:** 2026-10-14 · **Updated:** 2026-10-14

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-001.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-001-technical-plan.yaml`.

## Objective

Rebuild four Silver tables — `walk`, `observation`, `listing`, `listing_text` — as Apache Iceberg
tables from `landing/v1/`, validated against the pinned contract, deduplicated by total orders,
with descriptions redacted under a recorded version. Each rebuild commits one snapshot per table
whose id a later training dataset can pin. Development and CI run on a seeded synthetic landing
dataset; the operator's runs use an AWS Glue catalog over the existing private bucket.

## Context

- **Current state:**
    - This repository holds the workflow, CI, the leak guard and [ADR 0001](../../docs/adr/0001-silver-on-iceberg.md).
        `src/` is empty, `requirements.txt` has no runtime dependency, and there is no
        `contracts/` directory, although `CLAUDE.md` and the README already refer to
        `contracts/landing/v1/schema.json`.
    - The landing contract v1 exists in the private acquisition repository: four Parquet tables
        (`walk`, `observation`, `listing_detail`, `listing_text`) written as
        `landing/v1/<table>.parquet`, schema file SHA-256
        `f52989ab75f2321393082ea882a2b88c5630b51d22c71ff8af44335092369b72`. Its semantics document
        assigns deduplication, choosing a listing's newest record, days observed, disappearance
        events and redaction to Silver.
    - The private repository's superseded Silver plan (Parquet, read from Bronze) measured and
        reviewed the rules this plan reuses: the dedupe total orders, the empty-card rule and
        redaction `r1`. Its numbers are quoted below as prior evidence, not re-measured.
- **Problem:** no consumer can read listing history yet. Landing is deliberately raw (not
    deduplicated, unredacted, one row per Bronze record), so every notebook would otherwise repeat
    the same dedupe and redaction rules — and unredacted text would reach analytics code.
- **Constraints:**
    - Public repository: no real data, listing ids, URLs, bucket names or account ids in any file;
        the leak guard stays green.
    - Landing is read-only here and identified by its digest; v1 is never edited.
    - Free text is redacted before it enters Silver, under a recorded `redaction_version`.
    - Every Silver row carries `source`, the listing identifier, observation and ingestion time and
        `transformation_version`.
    - All instants UTC; money stays `INTEGER` euros.

## Scope

- **In scope:**
    - The byte-for-byte contract copy with its digest pinned in a test, and a public semantics
        README for it.
    - A synthetic landing writer and a seeded generator reproducing the hard cases.
    - Contract validation of landing input at the boundary, with specific errors.
    - Four Silver tables in the Iceberg namespace `vlc_housing_silver`: `walk`, `observation`,
        `listing`, `listing_text`.
    - An Iceberg sink that creates or overwrites them and records snapshot properties.
    - `rebuild()` and a CLI, run from the operator's laptop.
    - Terraform under `infra/silver/` for one Glue database whose location is a `silver/` prefix
        of the existing private bucket; a runbook; one operator run as validation.
- **Out of scope:**
    - `listing_event` (`disappeared` / `reappeared`): planned next as FEATURE-002 on top of these
        tables (decided by the user on 2026-10-14).
    - Merging listings that may be the same property (`property_cluster_id`).
    - Gold, training datasets, models, the web front end.
    - Snapshot expiry: nothing references a snapshot yet and volume is tiny (see cost); the policy
        is set by the first feature that pins a snapshot.
    - Iceberg schema evolution inside v1: a schema mismatch fails the rebuild loudly instead.
    - A scheduled or cloud-run rebuild, a least-privilege IAM role for the operator, reading
        landing directly from S3 (the operator syncs it to a local directory).
    - Redacting `title`, `location` or `features` (see Open assumptions).
- **Users / consumers:** the operator's notebooks (DuckDB, polars or PyIceberg reading the tables),
    and FEATURE-002.

## Dependencies

- **Needs:** landing v1 published by the private repository (done: its contract and first prod
    rebuild landed). Nothing in this repository.
- **Unblocks:** FEATURE-002 (`listing_event`), every Gold feature, and the first training dataset.

## Open assumptions

- **Assumption 1:** DuckDB query results go into PyIceberg tables without type loss for every type
    the contract uses (`VARCHAR`, `INTEGER`, `DOUBLE`, `BOOLEAN`, `DATE`,
    `TIMESTAMP WITH TIME ZONE`, `VARCHAR[]`), including an all-null `INTEGER` column. —
    **Evidence:** throwaway spike, 2026-10-14, scratchpad venv (DuckDB 1.5.6, PyIceberg 0.12.0,
    PyArrow 26.0.0, SQLAlchemy 2.1.4), `SqlCatalog` on SQLite with a `file://` warehouse:
    `con.sql(...).to_arrow_table()` → `catalog.create_table(schema=arrow.schema)` →
    `table.overwrite(arrow)`. The Iceberg schema came back as `string`, `int`, `double`, `boolean`,
    `date`, `timestamptz`, `list<string>`; the all-null column stayed `int`. **The first run failed:**
    DuckDB exports `TIMESTAMPTZ` in the session time zone (`timestamp[us, tz=Europe/Berlin]` on
    this laptop) and PyIceberg rejects any zone but UTC. After `SET TimeZone = 'UTC'` it passed.
    — **If wrong:** n/a; the finding becomes an invariant: `rebuild()` sets the session to UTC
    itself and a test runs it under a non-UTC session zone.
- **Assumption 2:** an overwrite yields a snapshot that time travel reproduces exactly. —
    **Evidence:** same spike. A table overwritten with 3 then 5 rows scans 3 rows at the first
    snapshot id and 5 at the current one. **An overwrite of a non-empty table writes two
    snapshots**, `DELETE` (0 rows) then `APPEND`; `snapshot_properties` land on both. So "one
    snapshot per rebuild" means the table's `current_snapshot()` after the commit, and that is the
    id the rebuild reports — never the intermediate `DELETE`. DuckDB's `iceberg` extension also read
    the table through `metadata_location` (5 rows, instants back as `+00`), so notebooks need no
    PyIceberg. — **If wrong:** n/a, measured.
- **Assumption 3:** the four tables cannot be committed atomically together; PyIceberg commits per
    table. — **Evidence:** PyIceberg 0.12 offers transactions per table, not across tables
    (unvalidated beyond the API surface). — **If wrong:** nothing breaks. Mitigation by design:
    every Arrow table is computed before the first commit, so only a commit failure can leave
    tables at different rebuilds; each snapshot's summary carries the same `rebuild_started_at`,
    which makes a partial rebuild visible, and rerunning is idempotent.
- **Assumption 4:** PyIceberg's `GlueCatalog` with a `s3://` warehouse works from the operator's
    laptop with the operator's existing AWS credentials in `eu-central-1`, where the private bucket
    lives. — **Evidence:** **unvalidated.** `pyiceberg.catalog.glue` exists in 0.12.0 and needs the
    `glue` extra (`boto3`); no AWS call was made from this session. — **If wrong:** task 4.2's
    operator run fails before any table is written; the fallback is the local `SqlCatalog`, which
    the code supports anyway because tests use it, and the Glue database stays empty.
- **Assumption 5:** a listing's `(source, source_listing_id, observed_date)` repeats in landing
    observations only as padding copies in neighbouring areas, and two copies can tie on every
    quality key. — **Evidence:** prior measurement in the private repository (2026-10-04): 18,166
    rows, 18,156 distinct pairs; one listing existed on one day only as two empty cards in two
    areas. Landing v1 documents the same ("not deduplicated", padding). — **If wrong:** the dedupe
    is a no-op costing one window function.
- **Assumption 6:** redaction `r1` (phone numbers and honorific + name, in descriptions only) removes
    the personal data the corpus carries, and titles, `location` and `features` carry none. —
    **Evidence:** prior counts-only census (2026-10-06): the phone pattern matched 418 of 14,253
    descriptions and 0 titles, with 0 residual numbers under a stricter probe; honorific hits in
    titles were street names. `location` and `features` were not measured. — **If wrong:** a
    missed format leaks into `listing_text`; a pattern change is `r2`, recorded per row.
    `location` and `features` are flagged as an open question for the Reviewer.
- **Assumption 7:** the whole corpus rebuilds in seconds. — **Evidence:** the prior spike built the
    equivalent tables from Bronze in 0.13 s and redaction added 0.17 s; this spike's create, two
    overwrites and two scans on SQLite took 2.1 s, dominated by catalog commits. Glue adds network
    round trips per commit. — **If wrong:** still minutes at worst; no design change.

## Design

- **Primary approach:** the SQL is the transformation; Python wires it.
    - `src/silver/landing.py` registers each `landing/v1/<table>.parquet` as a DuckDB view after
        checking `DESCRIBE` against `contracts/landing/v1/schema.json`.
    - `src/silver/sql/<table>.sql` (plus `redact.sql` for the macro) each produce one Silver table.
    - `src/silver/sink.py` writes the Arrow results to Iceberg.
    - `src/silver/rebuild.py` composes them and holds the CLI.
- **Boundaries and data flow:**

    ```
    landing/v1/*.parquet ──landing.py (contract check)──► DuckDB views
        ──sql/*.sql──► Arrow tables (all computed first)
        ──sink.py──► Iceberg: vlc_housing_silver.{walk, observation, listing, listing_text}
    ```

    - `rebuild(connection, catalog, *, landing, now)` takes an injected DuckDB connection, PyIceberg
        catalog and clock. It knows no AWS.
    - The CLI is the composition root: `python -m src.silver.rebuild --landing <dir> --catalog <name>`
        resolves `<name>` through PyIceberg's own configuration (`~/.pyiceberg.yaml` or
        `PYICEBERG_CATALOG__<NAME>__*` environment variables). Account-specific values therefore live
        only in the operator's environment, never in this repository, and the CLI test drives a
        SQLite catalog through environment variables.
    - `src/synthetic/landing.py` depends only on the contract file and DuckDB; Silver code never
        imports it.
- **Contracts and invariants:**
    - **Input:** a landing table conforms when `DESCRIBE SELECT * FROM read_parquet(<file>)` equals
        the contract's `(name, type)` list in order. A missing file, a column difference or a null in
        a key column (`source`, `area`, `source_listing_id`, `observed_at`, `observed_date`) raises
        `LandingContractError` naming the table and the difference or null count. No row is dropped
        silently.
    - **Common columns:** every Silver row carries `source` and
        `transformation_version = 'silver-v1'`. Every table except `walk` carries `source_listing_id`,
        `observed_at` and `ingested_at` of the landing record it was taken from.
    - **`walk`:** one row per landing `walk` row, every contract column as given, plus
        `transformation_version`. Silver does not re-derive `complete` or `is_replay`; landing owns
        both.
    - **`observation`:** one row per `(source, source_listing_id, observed_date)`, carrying `area`
        and `is_empty_card`. The survivor is chosen by a total order: `is_empty_card`,
        `price_eur DESC NULLS LAST`, `municipality`, `district NULLS FIRST`, `operation`,
        `observed_at`, `ingested_at NULLS LAST`. An empty card is kept, never filtered.
    - **`listing`:** one row per `(source, source_listing_id)` seen in `observation` or
        `listing_detail`.
        - It carries every typed `listing_detail` attribute from the newest detail record, by a
            total order: `observed_at DESC`, `ingested_at DESC NULLS LAST`, `municipality`,
            `district NULLS FIRST`, `operation`.
        - It adds `first_seen`, `last_seen` (dates) and `days_observed` from Silver `observation`,
            and `detail_observed_at`, the `observed_at` of the chosen detail record.
        - Attributes are null for a listing without any detail record; the seen columns are null
            for a detail-only listing. No free-text column.
    - **`listing_text`:** one row per listing that has a detail record.
        - It is taken from the **same** record `listing` chose, joined on
            `(area, source_listing_id, observed_at)` as the contract specifies.
        - `title`, `location` and `features` are verbatim; `description` passes through
            `redact_personal`; plus `redaction_version = 'r1'`.
        - A chosen detail record without a text row is a `LandingContractError`: the contract
            promises one-to-one.
    - **Redaction `r1`:** `regexp_replace(..., 'g')` of `(\+34[ .-]?|0034[ .-]?|\b)[6789]([ .-]?[0-9]){8}`
        with `[phone]` and of `\b(Sr|Sra|Srta|Don|Doña|Dña)\.?\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+` with `[name]`.
        Without `'g'` DuckDB replaces only the first match.
    - **Sink:** a missing namespace or table is created from the Arrow schema; an existing table is
        overwritten when its schema equals the new one, else `SilverSchemaError`. Each commit carries
        snapshot properties `transformation_version`, `redaction_version`, `landing_contract_sha256`
        and `rebuild_started_at` (ISO-8601 UTC, from the injected clock). Rows are written sorted on
        each table's access key: `walk (area, run_started_at)`, `observation (source_listing_id,
        observed_date)`, `listing` and `listing_text (source_listing_id)`.
    - **Result:** `RebuildResult` (frozen dataclass) maps each table to its row count and its
        `current_snapshot().snapshot_id`; the CLI prints both.
    - **Idempotency** holds on row sets, not bytes: every window ordering is total, so two rebuilds
        over the same landing produce equal tables.
    - The DuckDB session runs with `TimeZone = 'UTC'`, set by `rebuild()` (Assumption 1).
- **OOP / SOLID:** pure functions and direct composition. The external boundaries — DuckDB
    connection, Iceberg catalog, clock — are injected (DIP at the composition root). Two frozen
    dataclasses only: `RebuildResult`, and the parsed contract (`table → [(name, type)]`) if a plain
    dict proves unclear. Two exception types carry the two boundary failures.
- **Patterns:** none named. Injection of connection, catalog and clock is plain dependency
    injection.
- **Deliberately rejected complexity:**
    - No `TableSink` interface: ADR 0001 justifies one when Gold's Parquet sink exists; with one
        sink, a function is enough.
    - No ORM or dataframe layer, no `SilverTable` class over statements that share no behaviour.
    - No incremental rebuild, no partitioning (the corpus is megabytes).
    - No S3 Tables: its managed snapshot maintenance would expire snapshots on its own policy, while
        ADR 0001 requires that pinned snapshots be kept; a table bucket is also a new resource. Glue
        over the existing bucket keeps snapshot lifetime under this repository's control.
    - No DuckDB `httpfs` read of landing from S3: one `aws s3 sync` is the documented path.
    - No Python redaction library: two `regexp_replace` calls in one macro, versioned by a string.

## Approach

### Phase 1 — the contract and a synthetic landing

- [ ] **1.1 Contract copy, pinned.**
    - RED: `tests/test_landing_contract.py` asserts the SHA-256 of
        `contracts/landing/v1/schema.json` equals the pinned digest above; it fails because the file
        does not exist.
    - GREEN: copy `schema.json` byte-for-byte from the private repository (verified clean against
        the leak guard on 2026-10-14). Write `contracts/landing/v1/README.md`: grain and semantics of
        each table, what landing does not do, the v2 rule — without naming the source, the
        provider or private paths.
    - REFACTOR: none expected.
- [ ] **1.2 Writing landing tables from rows.**
    - RED: `write_landing_tables(directory, rows_by_table)` given one `walk` row writes
        `walk.parquet` whose `DESCRIBE` equals the contract, with absent columns null and an
        all-null `INTEGER` column still `INTEGER`; an unknown table or column name raises
        `ValueError`. Fails because `src/synthetic/` does not exist.
    - GREEN: `src/synthetic/landing.py` building a typed `SELECT` from the contract; add
        `duckdb==1.5.6` to `requirements.txt`.
    - REFACTOR: the contract reader lives in one place for both `src/synthetic/` and `src/silver/`
        (`src/contracts.py`).
- [ ] **1.3 The seeded generator.**
    - RED: `generate_landing(directory, seed)` writes all four tables, each conforming to the
        contract. It contains:
        - a recorded and an inferred replay, and a legacy walk with null run instants;
        - a two-area run crossing midnight;
        - an empty card;
        - one listing on one day in two areas (priced + empty) and one only as two empty cards;
        - a listing with detail records on two days, and one with two detail records at the same
            `observed_at` in two areas;
        - descriptions built from synthetic phone numbers and names.

        Two calls with the same seed give equal row sets, two seeds give different filler, and
        every id starts with `syn-`. Fails because the function does not exist.
    - GREEN: hand-written scenario rows plus seeded filler via `random.Random(seed)`.
    - REFACTOR: name each scenario.

### Phase 2 — the Silver transformation, in DuckDB

- [ ] **2.1 Landing is checked at the boundary.**
    - RED: `register_landing(connection, directory)` registers four views for a conforming
        directory, and raises `LandingContractError` for:
        - a missing table file;
        - a reordered column;
        - a column of the wrong type;
        - a null `source_listing_id` (message carries table and count, no values).

        Fails because `src/silver/` does not exist.
    - GREEN: `src/silver/landing.py`.
    - REFACTOR: none expected.
- [ ] **2.2 `walk`.**
    - RED: a three-report fixture — two areas of one run, a legacy report with null run instants
        and a recorded replay — comes out as three rows with every contract column unchanged and
        `transformation_version = 'silver-v1'`; dtypes asserted. Fails because no `walk.sql` exists.
    - GREEN: `sql/walk.sql` and a `build_tables(connection) -> dict[str, pa.Table]`.
    - REFACTOR: none expected.
- [ ] **2.3 `observation`, deduplicated by a total order.**
    - RED fixtures:
        - priced + empty copy of one id-day in two areas → the priced copy survives,
            `is_empty_card` false;
        - two empty copies in two areas → the alphabetically first municipality survives, on every
            run;
        - a lone empty card → kept, `is_empty_card` true;
        - all-null `price_eur` → still `int32`.

        Fails because no `observation.sql` exists.
    - GREEN: `sql/observation.sql` with the ranking window.
    - REFACTOR: none expected.
- [ ] **2.4 `listing`.**
    - RED fixtures:
        - detail on two days → newer attributes and `detail_observed_at`;
        - same `observed_at` and `ingested_at` in two areas → first municipality;
        - `first_seen`, `last_seen`, `days_observed` from deduplicated observations;
        - an observation-only listing (attributes null) and a detail-only listing (seen columns
            null);
        - no `title`, `description`, `location` or `features` column.

        Fails because no `listing.sql` exists.
    - GREEN: `sql/listing.sql`.
    - REFACTOR: extract the chosen-detail CTE if 2.5 needs the same one.
- [ ] **2.5 `listing_text` from the chosen record.**
    - RED:
        - one row per listing with detail, from the record `listing` chose (asserted on the
            two-day fixture: the newer text);
        - verbatim `title`, `location`, `features`;
        - a chosen record without a text row raises `LandingContractError`.

        Fails because no `listing_text.sql` exists.
    - GREEN: `sql/listing_text.sql`, sharing the chosen-detail CTE.
    - REFACTOR: none expected.
- [ ] **2.6 Redaction `r1`.**
    - RED, on synthetic values only:
        - `612 345 678`, `+34612345678.`, `0034 612-345-678`, `+34 712 345 678h` and `Sra. García`
            become `[phone]` / `[name]`;
        - one description with two numbers and a name has all three replaced (fails without
            `'g'`);
        - `690.000 €`, `Ref 123456789`, `reformado 2023` survive;
        - a title `calle Don Juan` stays verbatim;
        - every row carries `redaction_version = 'r1'`.

        Fails because 2.5 copies descriptions verbatim.
    - GREEN: `sql/redact.sql` with the `redact_personal` macro, applied in `listing_text.sql`.
    - REFACTOR: none expected.

### Phase 3 — the Iceberg sink and the rebuild

- [ ] **3.1 The sink.**
    - RED, `write_silver_tables(catalog, tables, properties)` on a temporary `SqlCatalog`:
        - it creates the namespace and tables and returns each table's current snapshot id;
        - a second write with new rows overwrites, and scanning the first id still returns the first
            rows;
        - both commits carry the given properties;
        - a write with a changed schema raises `SilverSchemaError` and leaves the table unchanged.

        Fails because `src/silver/sink.py` does not exist.
    - GREEN: the sink; add `pyiceberg[pyarrow,sql-sqlite,glue]==0.12.0` to `requirements.txt`.
    - REFACTOR: none expected.
- [ ] **3.2 `rebuild()` end to end on synthetic landing.**
    - RED: over `generate_landing(tmp, seed=1)`, `rebuild(connection, catalog, landing=tmp, now=fixed)`
        returns a `RebuildResult` whose row counts equal each table's scan. Snapshot properties carry
        the pinned contract digest and the fixed instant. It works when the connection's session
        zone is `Europe/Madrid` (Assumption 1). Fails because `rebuild` does not exist.
    - GREEN: `src/silver/rebuild.py` composing 2.1, `build_tables` and 3.1; set `TimeZone`.
    - REFACTOR: none expected.
    - **Regression guard**, not a RED: two rebuilds give equal row sets per table.
- [ ] **3.3 The CLI.**
    - RED, through `subprocess`:
        - `python -m src.silver.rebuild --landing <tmp> --catalog test`, with
            `PYICEBERG_CATALOG__TEST__*` pointing at a SQLite catalog, exits 0 and prints each table
            with its row count and snapshot id;
        - a missing `--landing` directory exits 2 with a message;
        - a non-conforming landing exits 1 naming the table.

        Fails because there is no `main`.
    - GREEN: `argparse` `main()`.
    - REFACTOR: none expected.

### Phase 4 — the operator's catalog and the runbook

- [ ] **4.1 Glue database as code.** Validation-first, not a behaviour task:
    - `infra/silver/`: `aws_glue_catalog_database` `vlc_housing_silver` with
        `location_uri = "s3://${var.lake_bucket}/silver/"`.
    - `var.lake_bucket` has no default, `var.region` defaults to `eu-central-1`.
    - S3 backend with partial configuration: the state bucket goes in via `-backend-config` from a
        gitignored file; `backend.hcl.example` shows the shape.
    - Validation: `terraform fmt -check` and `terraform validate` (after `init -backend=false`)
        pass. CI gets a `terraform-validate` job running exactly those commands; the leak guard
        stays green.
    - The operator then runs `plan`, which shows exactly one resource to add, and `apply`.
- [ ] **4.2 Runbook and one operator run.**
    - Write `docs/silver-rebuild.md`:
        - sync landing down;
        - the `~/.pyiceberg.yaml` entry for Glue (placeholders only);
        - the command, the four schemas and their rules, the snapshot properties;
        - reading a pinned snapshot from DuckDB and PyIceberg;
        - why a rebuild's snapshot is the post-commit `APPEND`, not the `DELETE`;
        - the partial-rebuild signal (Assumption 3).
    - Update the README status line.
    - Operator run against real landing, recorded in the progress log as counts only: row counts
        per table, `observation` ≤ landing `observation`, `listing_text` rows = `listing` rows with
        detail, and the residual-phone probe over Silver `listing_text` = 0. This falsifies or
        confirms Assumption 4.
    - Run `python dev/tools/validate_workflow.py`.

## Files

- **Create:** `contracts/landing/v1/schema.json` (byte copy), `contracts/landing/v1/README.md`.
- **Create:** `src/__init__.py`, `src/contracts.py` — the contract reader.
- **Create:** `src/synthetic/__init__.py`, `src/synthetic/landing.py`.
- **Create:** `src/silver/__init__.py`, `src/silver/landing.py`, `src/silver/sink.py`,
    `src/silver/rebuild.py`.
- **Create:** `src/silver/sql/{walk,observation,listing,listing_text,redact}.sql`.
- **Create:** `infra/silver/{main,variables,providers,backend}.tf`, `infra/silver/backend.hcl.example`,
    `.terraform.lock.hcl`.
- **Create:** `docs/silver-rebuild.md`.
- **Change:** `requirements.txt` — `duckdb`, `pyiceberg[pyarrow,sql-sqlite,glue]`, exact pins.
- **Change:** `.github/workflows/ci.yml` — the `terraform-validate` job.
- **Change:** `README.md` — the status line; `src/.gitkeep` — removed once `src/` has files.
- **Change:** `dev/plans/README.md` — registry row.
- **Tests:** `tests/test_landing_contract.py`, `tests/test_synthetic_landing.py`,
    `tests/test_silver_landing.py`, `tests/test_silver_{walk,observation,listing,listing_text,redaction}.py`,
    `tests/test_silver_sink.py`, `tests/test_silver_rebuild.py`, `tests/test_silver_cli.py`.

## Test strategy

- **Unit:** each SQL file over a few rows written with `write_landing_tables`. Each test covers the
    happy path, ties, nulls, and empty cards; dtypes are asserted on every table.
- **Contract / fixtures:** the digest pin; `DESCRIBE` equality for every generated table; malformed
    landing (missing file, reordered column, wrong type, null key). All fixtures are synthetic,
    with `syn-` ids and invented areas and text.
- **Integration:** `rebuild()` and the CLI over the seeded generator into a `SqlCatalog` in a
    temporary directory. No network and no AWS; the clock is fixed.
- **Retries / idempotency / evolution:** two rebuilds yield equal row sets; time travel to the
    first snapshot after a second rebuild; a schema change raises `SilverSchemaError`; a non-UTC
    session zone.
- **Configured quality checks:**
    - `python -m unittest discover -s tests -t . -v`
    - `python dev/tools/check_leaks.py`
    - `python dev/tools/validate_workflow.py`
    - `ruff check src tests dev && ruff format --check src tests dev`
    - from 4.1: `terraform -chdir=infra/silver fmt -check` and `validate`
- **Manual:** the operator run in 4.2.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses into this repository. Real landing and Silver stay in
    the private bucket and on the operator's laptop; only code, the contract and synthetic data are
    committed.
- **Privacy / security:**
    - Descriptions are redacted before Silver, under `r1`.
    - Errors report counts and table names, never values.
    - No credentials in code or config: the catalog resolves from the operator's environment, and
        the bucket name only reaches Terraform through a gitignored variables file.
    - The operator's existing credentials are used; a least-privilege policy is deferred.
- **Observability:** the CLI prints per-table row counts and snapshot ids. Snapshot summaries carry
    the versions, contract digest and rebuild instant.
- **Failure and recovery:** a contract failure stops before any commit. A commit failure can leave
    tables at different rebuilds, which is visible via `rebuild_started_at`; recovery is a rerun.
    Rollback is Iceberg's `manage_snapshots().rollback_to(...)`, documented in the runbook.
- **Migration / compatibility:** none; first version. Landing v2 would arrive as a new contract
    file and a reviewed change to `src/silver/landing.py`.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Glue Data Catalog | first 1M objects stored and 1M requests/month free | 1 database, 4 tables, < 1,000 requests per month (≈ 20 per rebuild, a few dozen rebuilds) | $0.00 |
| S3 Standard storage (existing bucket, `silver/` prefix) | $0.0245/GB-month (eu-central-1) | ≈ 8 MB per rebuild, 30 rebuilds kept with no expiry → ≈ 0.25 GB after a month | ~$0.01 |
| S3 requests | $0.0054 per 1,000 PUT, $0.00043 per 1,000 GET | ≈ 40 PUT and 100 GET per rebuild × 30 | ~$0.01 |
| **Total (new AWS components)** | | | **~$0.02/month** |

- **Cost drivers & cheaper alternatives:**
    - Storage grows linearly because nothing expires; even daily rebuilds for a year stay under
        3 GB (≈ $0.07/month).
    - The cheaper alternative is the local `SqlCatalog` with a laptop warehouse: $0, at the price
        of the catalog living on one machine.
    - S3 Tables was rejected on snapshot control (Design), not on cost.
- **External / non-AWS costs:** none.
- **Budget check:** yes; negligible against any monthly target.

## Success criteria

- [ ] The contract copy's SHA-256 equals the private repository's pin, asserted in a test.
- [ ] The seeded generator writes four conforming tables containing every named hard case,
    deterministically.
- [ ] Non-conforming landing fails with `LandingContractError` before any Iceberg commit.
- [ ] `observation` is unique on `(source, source_listing_id, observed_date)`, and `listing` and
    `listing_text` are unique on `(source, source_listing_id)`; two rebuilds give equal row sets.
- [ ] No synthetic phone number or honorific name survives in `listing_text.description`; every
    row has `redaction_version = 'r1'`.
- [ ] Each rebuild reports per table a snapshot id that time travel reproduces, with the contract
    digest and versions in its summary.
- [ ] `terraform validate` passes in CI, and the operator's `plan` adds exactly one Glue database.
- [ ] One operator run against real landing, recorded as counts only.
- [ ] Every behavior was implemented test-first, and focused plus affected suites pass.
- [ ] Configured type, lint, format, infrastructure, leak-guard and workflow checks pass.
- [ ] Configured coverage threshold is met, if one exists (none is configured).
- [ ] Data, operational, migration and cost contracts are documented where affected.
- [ ] No speculative scope, secrets, personal data or generated clutter is introduced.

## Open questions & risks

- **Question (Reviewer):** should `location` and `features` also pass through redaction? They were
    not measured (Assumption 6). Default: verbatim in `r1`; the 4.2 operator run adds a counts-only
    phone/honorific probe over both, and a hit means `r2`.
- **Question (operator, before 4.1):** which state bucket and key the Terraform backend uses.
    Default: the private repository's state bucket, key `vlc-housing-oracle/silver/terraform.tfstate`.
- **Risk:** the Glue path is unvalidated (Assumption 4). — *Mitigation:* it is the last task, the
    local catalog path is fully tested, and the Glue database is the only resource.
- **Risk:** cross-table non-atomic commits (Assumption 3). — *Mitigation:* compute all tables first;
    detect with `rebuild_started_at`; rerun.
- **Risk:** `pyarrow` (≈ 126 MB) and `boto3` enlarge the CI install. — *Mitigation:* pip cache in CI
    already exists; accepted.
- **Risk:** the copied contract README semantics drift from the private original. — *Mitigation:*
    only `schema.json` is pinned and authoritative; the README here describes meaning in this
    repository's words and links nothing private.

## Progress log

- **2026-10-14** — Planned. Spike (scratchpad, discarded) confirmed DuckDB → PyIceberg types, time
    travel and snapshot properties, found the UTC session requirement and the two-snapshot overwrite.
    User chose a managed catalog (Glue selected over S3 Tables, see Design) and split
    `listing_event` into FEATURE-002.
