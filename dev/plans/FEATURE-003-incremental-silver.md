# FEATURE-003 — Incremental Silver from landing v2, run by AWS

**Status:** ⚪ Superseded · **Effort:** L (~20-24h) · **Priority:** High
**Branch root:** `feature/incremental-silver` · **Created:** 2026-10-16 · **Updated:** 2026-10-17

> **Superseded on 2026-10-18 by [ADR 0003](../../docs/adr/0003-immutable-bronze-and-landing-per-run.md).**
> Landing v2 and its manifests were retired before Silver read them. The successor feature plans
> incremental Silver against [landing v3](../../contracts/landing/v3/README.md): commits per
> collection run, an ingest log, a set difference instead of a checkpoint id. Much of this plan's
> Silver-side design (day replacement, affected listings, the Fargate task, the trigger shape)
> carries over; it is kept as the record.

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-003.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-003-technical-plan.yaml`.

## Objective

Silver stops rebuilding history every time. Each night a landing v2 manifest starts a Silver task
on ECS Fargate that replaces only the day partitions named by the manifests it has not yet
processed, and updates only the listings those days touch. Every commit records the manifest it
processed, so a missed night is caught up automatically. A full rebuild remains as a deliberate
backfill. Failures are emailed.

FEATURE-002 stays reserved for `listing_event`.

## Context

- **Decision record:** [ADR 0002](../../docs/adr/0002-incremental-processing.md) (incremental by day
    partition, manifests as the change log, checkpoint in the output, full rebuild only as backfill),
    which amends [ADR 0001](../../docs/adr/0001-silver-on-iceberg.md). The landing v2 contract is in
    [`contracts/landing/v2/`](../../contracts/landing/v2/README.md): the same columns as v1, the
    layout `landing/v2/<table>/observed_date=<d>/part-0.parquet`, and immutable manifests under
    `landing/v2/_manifests/`. Its `schema.json` SHA-256 is `32f5ca67…387d` (proposed; this feature
    pins it).
- **Current state (FEATURE-001):**
    - `rebuild()` validates landing v1 (`src/silver/landing.py`) and computes four tables in DuckDB
        (`src/silver/sql/`), each table whole.
    - It commits them to Glue through `write_silver_tables`, which runs one `overwrite` per table.
    - The four tables are unpartitioned.
    - The CLI reads a local directory.
    - First operator run 2026-10-16; Athena reads the tables.
- **Writer side:** the acquisition repository's FEATURE-015 writes landing v2 incrementally each
    night at 00:30 UTC, with a weekly full v2 run as a safety net, and the lake bucket delivers S3
    events to EventBridge.
- **Evidence for the write path** (spike 2026-10-17, PyIceberg 0.12.0, local SQLite catalog):
    - On a table partitioned by `identity(observed_date)`, `overwrite(df, overwrite_filter=In(
        "observed_date", days))` replaced exactly the filtered day and kept four others.
    - `upsert(df, join_cols=["id"])` updated and inserted correctly on a table with a
        `list<string>` column.
    - Every resulting snapshot carried the given `snapshot_properties`.
    - Writing a `month` transform raised `NotInstalledError` without `pyiceberg-core`. Identity day
        partitions need no extra dependency.
- **Infrastructure facts** (2026-10-15/16):
    - The collector runs on Fargate in the default VPC with a public IP and no NAT gateway.
    - The lake bucket is SSE-S3, unversioned, and its lifecycle touches only `raw/`.
    - Glue commits are optimistic: `UpdateTable(VersionId=…)`, and a conflict raises
        `CommitFailedException` (`pyiceberg/catalog/glue.py:410-415`).
- **Constraints:**
    - Public repository: no account values in any committed file; they are passed through a
        gitignored `terraform.tfvars`.
    - CI never holds AWS credentials; the image goes to GHCR (decided 2026-10-16).
    - Correctness and data integrity outrank speed: the incremental result must equal a backfill.

## Scope

- **In scope:**
    - **Contract:** pin landing v2. Silver reads v2 only, and v1 reading is removed.
    - **Synthetic landing v2:** day partitions plus manifests, so every test runs without real
        data. This includes a "replay" that rewrites an old day under a later manifest.
    - **A fifth Silver table, `listing_detail`:** one row per landing detail record, partitioned
        by day. It is the history the `listing` recompute reads, instead of rereading landing's
        whole history every night.
    - **Incremental mode** (the default):
        - read the checkpoint;
        - take the manifests after it;
        - replace the named days in `walk`, `observation` and `listing_detail`;
        - recompute and upsert `listing` and `listing_text` for the affected listings;
        - commit with the new checkpoint.
    - **Backfill mode:** every v2 day; the result is equal to an incremental history.
    - **Running in AWS:**
        - `--landing s3://…` with an injected S3 client;
        - the container image and its CI workflow to GHCR;
        - Terraform for the cluster, the task, least-privilege roles, the trigger on new manifests,
            and failure emails.
    - The runbook.
- **Out of scope:**
    - `listing_event` (FEATURE-002) and Gold.
    - Compaction and snapshot expiry: small daily files are accepted at this volume.
    - A missed-run alarm.
    - Month partitions (they need `pyiceberg-core`).
    - Automatic deploys on merge.
    - Schema evolution of landing.
- **Users / consumers:** the operator (Athena, notebooks, failure emails), FEATURE-002 and Gold.

## Dependencies

- **Needs:**
    - FEATURE-001 (pull request #1): the transformations, the boundary checks, the sink and the
        Glue database.
    - **Acquisition FEATURE-015:** landing v2 in S3 plus EventBridge delivery. Everything here is
        built and tested on synthetic v2 first. The AWS run (Phase 6) waits for FEATURE-015's
        backfill.
- **Unblocks:** FEATURE-002 running unattended, and every Gold feature.

## Open assumptions

- **Assumption 1:** incremental and backfill give equal tables. — **Evidence:** by construction, the
    days are replaced whole and the affected listings are recomputed from complete history. —
    **Falsified by:** a regression test that replays a sequence of synthetic manifests incrementally
    and compares with a backfill of the final landing, table by table and row set by row set
    (task 3.4).
- **Assumption 2:** the "affected listings" rule is complete. A listing's `listing` and
    `listing_text` rows can only change when one of its observation or detail records changes, and
    every such record lives in a day the manifests name. — **Evidence:** `listing` is a function of
    that listing's own observation and detail rows (FEATURE-001 SQL). The contract guarantees
    manifests name every rewritten day. — **If wrong:** a listing keeps stale attributes until a
    backfill. The task 3.4 equality test covers replays and moved records.
- **Assumption 3:** a nightly recompute of affected listings can afford to scan the
    `source_listing_id` column of the whole Silver `observation` and `listing_detail`. —
    **Evidence:** about 6.5 million observation rows after five years, read column-pruned by
    PyIceberg with a row filter on the affected ids; not measured. — **If wrong:** the scan grows
    slow; then sort or bucket by `source_listing_id`, a later optimisation that changes no contract.
- **Assumption 4:** Fargate pulls a public GHCR image anonymously. The operator sets the new package
    public once, and `ubuntu-24.04-arm` runners build natively. — **Evidence:** unvalidated. — **If
    wrong:** `CannotPullContainerError` is emailed. Fallbacks: an ECR pull-through cache, or QEMU on
    `ubuntu-latest`.
- **Assumption 5:** PyIceberg's Glue catalog and PyArrow's S3 file IO use the ECS task role. —
    **Evidence:** boto3 does by design; PyArrow's AWS SDK default chain should, but it was not
    observed. — **If wrong:** the first manual `run-task` fails with an access error. Fallback:
    pass boto3's resolved credentials as FileIO properties at the composition root.
- **Assumption 6:** 1 vCPU / 2 GB suffice. — **Evidence:** a night touches one or two days, and the
    widest scan is two id columns; not measured. Docker was not running. — **If wrong:** exit 137 is
    emailed; raise one variable.

## Design

- **Primary approach:** keep FEATURE-001's SQL as the rules, and change what they run over:
    - from "all of landing" to "the named days" for the per-day tables;
    - from "all listings" to "the affected listings" for the history-wide tables.

    One code path serves both modes. A backfill is incremental with every day named and every
    listing affected.
- **Data flow of one incremental run:**

    ```
    checkpoint = min over the five tables of snapshot property landing_manifest_id
    manifests  = landing/v2/_manifests/* with id > checkpoint, in id order     (none → exit 0)
    days[t]    = union of manifests' partitions[t]
    fetch      landing/v2/<t>/observed_date=<d>/part-0.parquet for d in days[t]
    check      contract columns per file; manifest row_counts (newest manifest per day);
               v1's null, uniqueness and detail/text pairing rules on the fetched days
    per-day    walk, observation, listing_detail ← SQL over the fetched days
               commit each: overwrite(filter observed_date IN days[t])
    affected   = source_listing_id in the fetched observation ∪ listing_detail days
    listing    = recompute for affected ids from Silver observation + Silver listing_detail
               (all days, row-filtered to affected ids), same SQL rules as FEATURE-001
    text       = fetch the landing listing_text days of each affected listing's chosen record,
               redact r1; upsert listing, upsert listing_text (join source, source_listing_id)
    every commit: landing_manifest_id = newest manifest id, transformation_version, redaction_version
    ```

- **Contracts and invariants:**
    - **Silver tables** in `vlc_housing_silver`:
        - `walk`, `observation` and the new `listing_detail` are partitioned by
            `identity(observed_date)`, with columns as in FEATURE-001; `listing_detail` carries every
            landing `listing_detail` column plus `transformation_version`.
        - `listing` and `listing_text` are unpartitioned, with columns as in FEATURE-001.
        - The existing unpartitioned tables are evolved with `update_spec()` on the first run of this
            feature, which is a backfill.
    - **Checkpoint:**
        - Every snapshot written by a run carries `landing_manifest_id` = the newest manifest that
            run processed.
        - The run starts from the **minimum** over the five tables, so a run that failed after
            committing some tables is repeated for all of them. Repeating is idempotent, because
            days are replaced whole and listings are upserted from full history.
        - No table with a checkpoint means backfill.
    - **Consistency checks before any commit:**
        - every fetched file conforms to the v2 columns;
        - each day's row count equals the newest manifest's `row_counts` for that day, so a file
            rewritten since the manifest is caught and the run fails loudly;
        - v1's per-table non-null, uniqueness and pairing rules hold on the fetched days.

        All failures are a `LandingContractError` with counts only.
    - **Affected listings:** every `(source, source_listing_id)` appearing in a fetched
        `observation` or `listing_detail` day, **before and after** the change. Silver's previous
        rows of those days are read before the overwrite, so a listing that disappears from a
        rewritten day is recomputed too.
    - **Upsert keys:** `(source, source_listing_id)` for `listing` and `listing_text`.
    - **Idempotency:**
        - A manifest processed twice changes nothing.
        - Two concurrent runs: one loses the optimistic Glue commit (`CommitFailedException`), exits
            non-zero and is emailed; the next night repeats from the minimum checkpoint.
    - **Equality:** incremental through any sequence of manifests equals a backfill of the final
        landing, row set by row set, per table (task 3.4).
- **Running in AWS:**
    - `--landing s3://<bucket>/landing/v2/` downloads the needed manifests and day files through an
        injected S3 client into a temporary directory. `--mode incremental|backfill`, default
        incremental.
    - **Image:** `python:3.12-slim`, `requirements.txt`, `src/` and `contracts/`, non-root,
        `ENTRYPOINT ["python", "-m", "src.silver.rebuild"]`. Tag = commit SHA; the task runs
        `var.silver_image`, never `:main`.
    - **Task:**
        - ARM64, 1 vCPU / 2 GB;
        - environment `PYICEBERG_CATALOG__SILVER__TYPE=glue`,
            `PYICEBERG_CATALOG__SILVER__GLUE__REGION`, `AWS_REGION`;
        - default VPC public subnets with a public IP and an egress-only security group, as the
            collector.
    - **Trigger:** an EventBridge rule on `aws.s3` / `Object Created` for the lake bucket with key
        prefix `landing/v2/_manifests/` runs the task. A burst of manifests may start several runs;
        the losers fail the optimistic commit and are harmless.
    - **Roles:**
        - task: `s3:GetObject` on `landing/v2/*`; `s3:GetObject`, `PutObject` and `DeleteObject` on
            `silver/*`; `s3:ListBucket` limited to the `landing/v2/` and `silver/` prefixes; Glue
            `GetDatabase`, `GetTable`, `GetTables`, `CreateTable` and `UpdateTable` on the
            `vlc_housing_silver` database and its tables;
        - execution: logs only, because the image is public;
        - EventBridge: `ecs:RunTask` on this task definition and `iam:PassRole` on its two roles.
    - **Alerts:** a rule on the Silver cluster's `STOPPED` tasks with a non-zero exit, or a stop code
        other than `EssentialContainerExited`, publishes to an SNS topic with an email subscription
        (`var.alert_email`).
    - **Logs:** 30 days; counts, days and snapshot ids only.
- **OOP / SOLID:**
    - Pure functions: choosing manifests after a checkpoint, the union of days, the newest
        `row_counts` per day, and the affected listings.
    - The injected boundaries are the S3 client, the Iceberg catalog and the clock.
    - `RebuildResult` gains `landing_manifest_id`.
    - No new class beyond frozen value objects for a parsed manifest.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - **Checkpoints in DynamoDB:** not atomic with the commit (ADR 0002).
    - **Reading landing's whole detail history nightly:** Silver `listing_detail` holds it.
    - **Iceberg `MERGE` through Spark or Athena:** PyIceberg's `upsert` suffices.
    - **Month partitions:** they need a native dependency.
    - **Per-run Step Functions:** the user chose the event.
    - **Lambda:** it needs ECR, and the zip size limit is tight.
    - **A NAT gateway:** ≈ $35/month.

## Approach

### Phase 1 — landing v2 as input

- [ ] **1.1 Pin landing v2.** RED: a test asserts the digest of `contracts/landing/v2/schema.json`.
    It fails only if the file drifts, so it is written against the committed file and paired with
    a column-equality test against v1. GREEN: the test passes on the existing file;
    `src/contracts.py` gains `landing_v2_*` readers.
- [ ] **1.2 Synthetic landing v2 with manifests.**
    - RED: `generate_landing_v2(directory, seed)` writes v2 day files that conform to the contract,
        with exactly one file per table and day, plus a sequence of manifests whose ids sort in
        write order.
    - One later manifest rewrites an old day: a replay that changes a price and removes a
        listing's card.
    - GREEN: built on the existing `write_landing_tables` and scenarios.
- [ ] **1.3 Choosing manifests and days (pure).**
    - RED: for `pending_manifests(ids, checkpoint)` and
        `days_to_process(manifests) -> (days per table, expected row count per day from the newest
        manifest)`:
        - no checkpoint means all;
        - a checkpoint equal to the last id means none;
        - a gap is processed in order;
        - for a day named twice, the later count wins.
- [ ] **1.4 Fetch and check the named days.**
    - RED: `fetch_landing_v2(s3_client, uri, directory, checkpoint)` with a fake client:
        - it downloads exactly the pending manifests and the named day files;
        - a row count differing from the manifest raises `LandingContractError` naming table and
            day;
        - a missing day file raises;
        - a malformed manifest raises.
    - Then `register_landing` (v2) applies v1's per-table rules to the fetched days.
    - v1 reading is removed and its tests are migrated.

### Phase 2 — per-day tables, incrementally

- [ ] **2.1 Silver `listing_detail`.** RED: one row per landing detail record with every contract
    column plus `transformation_version`; dtypes asserted. GREEN: `sql/listing_detail.sql`.
- [ ] **2.2 Partitioned per-day tables and day replacement in the sink.**
    - RED on a SQLite catalog:
        - `write_days(table, rows, days, properties)` creates the table partitioned by
            `identity(observed_date)`, or evolves an unpartitioned table once;
        - it replaces exactly the named days, leaves the others, and carries the properties;
        - rows outside the named days raise.
    - The FEATURE-001 schema-name check is kept.
- [ ] **2.3 Checkpoint read.** RED: `checkpoint(catalog)` returns the minimum `landing_manifest_id`
    across the five tables; `None` when any table is missing or has none.

### Phase 3 — history-wide tables, incrementally

- [ ] **3.1 Affected listings.** RED: the ids in fetched `observation` and `listing_detail` days
    **and** in Silver's current rows of those days, so a listing removed by a replay is affected.
- [ ] **3.2 Recompute `listing` for affected ids.**
    - RED: from Silver `observation` and `listing_detail`, row-filtered to the affected ids, the
        recomputed rows equal FEATURE-001's `listing` rules for those ids.
    - The newest record moves to an older one when a replay removes the newest.
    - A listing with no rows left is deleted from `listing`.
    - GREEN: `listing.sql` reads Silver views instead of landing, and the sink gains
        `upsert_rows`, with deletes for vanished ids.
- [ ] **3.3 `listing_text` for affected ids.**
    - RED: the text of each affected listing's chosen record is fetched from its day's landing
        `listing_text` file, which may be an old day not in the manifests, then redacted under `r1`
        and upserted.
    - A vanished listing's text is deleted.
- [ ] **3.4 Incremental equals backfill.**
    - Regression guard over synthetic v2: apply the manifests one by one, including the replay,
        then compare every table with a backfill of the final landing.
    - Also: re-running the last manifest changes nothing, and a run that failed after two tables
        is completed correctly by the next run, through the minimum checkpoint.

### Phase 4 — the CLI

- [ ] **4.1 Modes and S3 input.** RED:
    - `main(["--landing", "s3://syn-bucket/landing/v2/", "--catalog", "test"], s3_client=fake)`
        runs incrementally, and prints per-table rows, days and the manifest id;
    - with nothing pending it exits 0 and prints "up to date";
    - `--mode backfill` processes every day;
    - local paths still work;
    - exit codes are unchanged.

### Phase 5 — image and infrastructure

- [ ] **5.1 Dockerfile and `.dockerignore`.** Validation-first: a `linux/arm64` build, and a
    container run over mounted synthetic v2 with a SQLite catalog that exits 0, runs non-root, and
    contains no `tests/` or `dev/`.
- [ ] **5.2 `.github/workflows/image.yml`.** Validation-first: on a PR it builds without pushing; on
    `main` it pushes `:<sha>` and `:main` with `GITHUB_TOKEN` (`packages: write`) and no AWS step.
- [ ] **5.3 Terraform: cluster, task definition, roles, logs, security group** (`infra/silver/ecs.tf`).
    Validation-first: `fmt`/`validate`; a policy review against the least-privilege list.
- [ ] **5.4 Terraform: trigger and alerts** (`infra/silver/trigger.tf`, `alerts.tf`, `trigger_enabled`).
    Validation-first: `validate`; the operator runs `aws events test-event-pattern` with a manifest
    key (match) and a day-file key (no match).

### Phase 6 — operation

- [ ] **6.1 First run in AWS** (operator, after acquisition FEATURE-015's v2 backfill):
    - set the package public, `apply`, confirm the email subscription;
    - run `aws ecs run-task --mode backfill` once, and check that all five tables carry the newest
        manifest id;
    - let one nightly manifest trigger an incremental run, and check that only recent days were
        replaced;
    - force one failure and check the email;
    - record counts and ids only in the progress log.
- [ ] **6.2 Runbook, README and contract notes:**
    - `docs/silver-rebuild.md`: modes, checkpoint, catching up, backfill after a rule change,
        deploying an image, disabling the trigger;
    - the README status.

## Files

- **Change:** `src/contracts.py`, `src/silver/landing.py` (v2 fetch and checks; v1 removed),
    `src/silver/rebuild.py` (modes, checkpoint, orchestration), `src/silver/sink.py` (`write_days`,
    `upsert_rows`, `checkpoint`), `src/silver/sql/listing.sql` (reads Silver views),
    `src/silver/sql/listing_text.sql`.
- **Create:** `src/silver/sql/listing_detail.sql`, `src/silver/manifests.py` (pure manifest and day
    selection), `src/synthetic/landing_v2.py` or additions to `src/synthetic/landing.py`.
- **Create:** `Dockerfile`, `.dockerignore`, `.github/workflows/image.yml`, `infra/silver/{ecs,trigger,alerts}.tf`.
- **Change:** `infra/silver/variables.tf`, `docs/silver-rebuild.md`, `README.md`,
    `dev/plans/README.md`.
- **Tests:** `tests/test_landing_contract.py`, `tests/test_synthetic_landing.py`,
    `tests/test_silver_manifests.py`, `tests/test_silver_landing.py`, `tests/test_silver_sink.py`,
    `tests/test_silver_listing*.py`, `tests/test_silver_incremental.py`, `tests/test_silver_cli.py`.

## Test strategy

- **Unit:**
    - manifest selection and the newest-count rule;
    - the affected-listing rule, including removal by a replay;
    - day replacement, upsert and delete on a SQLite catalog;
    - checkpoint minimum.
- **Contract / fixtures:**
    - the v2 digest pin;
    - v2 columns equal to v1;
    - synthetic v2 only: `syn-` ids, invented areas, visibly fake phone numbers.
- **Integration:**
    - incremental-equals-backfill over a manifest sequence with a replay (3.4);
    - the CLI with a fake S3 client;
    - a local container run;
    - the CI image build.
- **Retries / idempotency / evolution:**
    - a manifest processed twice;
    - partial failure completed by the minimum checkpoint;
    - concurrent commit loses cleanly (simulated by committing in between);
    - partition-spec evolution of an existing unpartitioned table.
- **Configured quality checks:**
    - `python -m unittest discover -s tests -t . -v`
    - `python dev/tools/check_leaks.py`
    - `python dev/tools/validate_workflow.py`
    - `ruff check src tests dev && ruff format --check src tests dev`
    - `terraform -chdir=infra/silver init -backend=false && … fmt -check && … validate`
- **Manual:** Phase 6.

## Operational and compliance impact

- **Data sourcing / legal:** unchanged; only landing crosses the boundary.
- **Privacy / security:**
    - Redaction still happens before Silver; `r1`'s known title residue is unchanged.
    - Public CI has no AWS credentials.
    - The roles are scoped to `landing/v2/`, `silver/` and one Glue database.
    - Account values live only in `terraform.tfvars`.
- **Observability:**
    - Logs carry manifest ids, days per table and counts.
    - Snapshot properties carry the checkpoint.
    - Failures are emailed.
    - A missed run is not detected (Risk).
- **Failure and recovery:**
    - A failed night is caught up by the next one, from the minimum checkpoint.
    - A backfill is `--mode backfill`.
    - Data rollback is `manage_snapshots().rollback_to(...)` per table, after which the next run
        repeats from the rolled-back checkpoint.
    - Code rollback is the previous image SHA.
- **Migration / compatibility:**
    - The first run is a backfill that evolves `walk` and `observation` to day partitions and
        creates `listing_detail`.
    - Silver stops reading v1; the acquisition side stops writing v1 afterwards.
    - Snapshot ids printed by FEATURE-001 stay readable by time travel.

## Estimated monthly cloud cost

| Component | Pricing basis (eu-central-1) | Assumption | Est. / month |
|---|---|---|---|
| Fargate ARM64 (Silver task) | ≈ $0.0372 per vCPU-h, ≈ $0.0041 per GB-h | 1 vCPU, 2 GB, ≈ 3 min/night including pull | ~$0.07 |
| Public IPv4 | $0.005 per IP-hour while running | ≈ 1.5 h | ~$0.01 |
| S3 GET (manifests, changed days, id-column scans of history) | $0.00043 per 1,000 | today ≈ 200/night; after 5 years ≈ 4,000/night with daily files | ~$0.01 now, ~$0.05 later |
| S3 PUT / DELETE (Iceberg data and metadata) | $0.0054 per 1,000 | ≈ 60/night | ~$0.01 |
| S3 storage growth in `silver/` | $0.0245 per GB-month | ≈ 1 MB/night incremental plus metadata, no expiry | ~$0.00 |
| Glue Data Catalog | free tier (1M objects / 1M requests) | ≈ 10 commits/night | $0.00 |
| EventBridge rules, S3 events, SNS email | service events not billed; 1,000 emails free | ≈ 30 runs | $0.00 |
| CloudWatch Logs | $0.63 per GB | < 1 MB | ~$0.00 |
| **Total (new AWS components, this repository)** | | | **~$0.10/month** |

- **Cost drivers & cheaper alternatives:**
    - Fargate minutes dominate.
    - The incremental design keeps nightly work flat. Its only growing part is the id-column scan
        over history, cents after five years, removable by sorting or compaction later.
    - The cheaper alternative is the manual laptop flow, at $0.
- **External / non-AWS costs:** GHCR and GitHub Actions for a public repository, $0. Acquisition
    FEATURE-015 adds ≈ $0.13/month on its side.
- **Budget check:** yes.

## Success criteria

- [ ] Landing v2 is pinned by digest, and Silver reads v2 only.
- [ ] An incremental run replaces exactly the days named by pending manifests and upserts exactly
    the affected listings; a run with nothing pending is a no-op. Covered by tests.
- [ ] Incremental through a manifest sequence, including a replay, equals a backfill of the final
    landing, row set by row set (task 3.4).
- [ ] Every commit carries `landing_manifest_id`. A partial failure is completed by the next run
    through the minimum checkpoint.
- [ ] The image builds for ARM64 in CI and is published by SHA on `main`, without AWS credentials.
- [ ] In AWS:
    - a backfill run and one manifest-triggered incremental run succeed;
    - a forced failure is emailed;
    - all of it is recorded as counts.
- [ ] Every behavior was implemented test-first; configured checks, the leak guard and
    `terraform validate` pass.
- [ ] Runbook, checkpoint semantics and cost are documented.

## Open questions & risks

- **Risk:** the id-column scan over history grows (Assumption 3). — *Mitigation:* measured in 6.1.
    Sorting or bucketing by `source_listing_id` is a later, contract-neutral optimisation.
- **Risk:** small files. Daily partitions mean about three new files per night per partitioned
    table. — *Mitigation:* harmless at this volume; a backfill rewrites compactly; a compaction
    job is a candidate follow-up.
- **Risk:** a silent stop if no manifest arrives. — *Mitigation:* the acquisition side emails its
    own failures, and a freshness alarm is a candidate follow-up.
- **Question (reviewer):** should the first AWS run be a backfill from v2, or should Silver keep its
    FEATURE-001 snapshots and only evolve the tables? Default: a backfill. It is simplest and proves
    v2 end to end, and the old snapshots stay readable.
- **Question (operator, before 6.1):** the failure email address, set in `terraform.tfvars` only.

## Progress log

- **2026-10-16** — First drafted as a scheduled whole rebuild triggered by a `_SUCCESS` marker.
- **2026-10-17** — Re-planned as incremental Silver after the operator rejected nightly whole
    rebuilds; see ADR 0002 and the landing v2 contract. Evidence: PyIceberg 0.12's filtered
    overwrite on identity day partitions, upsert with list columns, snapshot properties on every
    commit (spike); a month transform needs `pyiceberg-core`. Scheduling decisions of 2026-10-16
    (GHCR, event on landing, Fargate in the default VPC) carried over, with the trigger now on
    `landing/v2/_manifests/`.
