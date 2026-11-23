# FEATURE-020 — Run manifest, rollback and snapshot expiry

**Status:** 🔵 Planned · **Effort:** M (~14h) · **Priority:** High
**Branch root:** `feature/run-manifest` · **Created:** 2026-11-22 · **Updated:** 2026-11-22

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-020.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-020-technical-plan.yaml`.

## Objective

Give every Silver run one consistency point: a run manifest, written in the same commit as the
ingest log, that names the snapshot of every table. A training dataset records that one id, a
rollback command reads it, and snapshot expiry keeps exactly what a retained manifest names. The
policy must exist before the first dataset pins anything; old snapshots still hold `r1` text.

## Context

- **Current state:**
    - A run that processes something commits 12 to 14 times: three day tables
        (`src/silver/rebuild.py:267-277`), `listing` and `listing_text` (`:286-301`), six to
        eight whole-table replacements (`:309-316`), then the ingest log (`:317`). Each table is
        its own transaction (`src/silver/sink.py:40-51`).
    - The ids that belong together are printed (`src/silver/rebuild.py:875-896`) into a log kept
        30 days (`infra/silver/ecs.tf:48`). The ingest log's own commit records one property,
        `transformation_version` (`src/silver/ingest.py:96`).
    - ADR 0001 has a dataset record "the Silver `snapshot_id`" and names expiry as a duty
        (`docs/adr/0001-silver-on-iceberg.md:21-22`, `:32-33`); the runbook defers the policy
        (`docs/silver-rebuild.md:854-855`). Tables carry no property (`src/silver/sink.py:140`).
- **Problem (review items B1 and B4, each checked in the code):**
    - A reader between two commits, or after a run that stopped, joins tables of two runs. The
        quality queries take their set table by table "while no Silver task runs"
        (`analysis/silver-quality/README.md:44-50`).
    - The runbook says "Every Arrow table is computed before the first commit, so a refused run
        writes nothing" (`docs/silver-rebuild.md:115-116`). Both halves are wrong: the derived
        tables are computed after the day tables are committed (`src/silver/rebuild.py:302-316`),
        and a `SilverSchemaError` is raised per table at write time (`src/silver/sink.py:42`,
        `:129-148`), after earlier tables are committed.
    - Rollback is by hand per table, and the documented call does not exist: the runbook names
        `manage_snapshots().rollback_to(...)` (`docs/silver-rebuild.md:851`); PyIceberg 0.12.0 has
        `rollback_to_snapshot` and `set_current_snapshot` only.
    - Snapshots and files grow with every commit. The set of 2026-10-31 is still readable and
        holds `r1` text (`docs/data-quality/silver-quality-2026-11.md:24-27`; taken from the
        document, not checked against the lake).
- **What PyIceberg 0.12.0 offers (read in the installed package, then tried on a SQLite catalog):**
    - `table.maintenance.expire_snapshots()` with `by_ids` and `older_than`
        (`pyiceberg/table/maintenance.py:36`, `pyiceberg/table/update/snapshot.py:1286-1335`). It
        never expires a branch head or a tagged snapshot, and it changes metadata only: the file
        count on disk was the same before and after.
    - `manage_snapshots()` has `create_tag` and `remove_tag` (`:1068-1099`). Its
        `rollback_to_snapshot` refuses a snapshot whose parent chain an expiry broke;
        `set_current_snapshot` accepts it (`:1144-1201`).
    - `write.metadata.delete-after-commit.enabled` is honoured on every commit
        (`pyiceberg/catalog/__init__.py:859-871`): with three previous versions allowed, ten
        overwrites left four metadata files, also on a table that got the property later.
    - No orphan-file removal, no rewrite of data files, no rewrite of manifests.
- **Constraints:** a run's checkpoint is stored with its output, written last, not in a separate
    store. The lake bucket is unversioned, so a deleted file is gone. Under $1 a month today.

## Scope

- **In scope:** the manifest in the ingest log's commit and its readers; one report line; a
    maintenance command with `list`, `rollback`, `pin` and `expire`; metadata-file clean-up by
    table property; the retention policy in the runbook and ADR 0001; operator steps for file
    removal and compaction with Athena, after a check on a scratch table.
- **Out of scope:**
    - making a run atomic across tables: Iceberg has no multi-table commit, and a pre-check of
        every schema is impossible while derived tables are computed after the first writes;
    - the labeller reading through the manifest (`src/enrichment/text_labels.py:351-354`):
        FEATURE-017 changes that module now; two lines once a manifest exists in production;
    - a schedule or task definition for maintenance, a writer lease (FEATURE-023), and `unpin`
        (`remove_tag` by hand until a dataset is retired);
    - retention and erasure of text beyond what this expiry removes (FEATURE-024);
    - compaction code and rewriting the ingest log within a run: both wait for the file counts
        of task 4.3 and FEATURE-016's run durations.
- **Users / consumers:** the first Gold or training dataset, the quality queries, the operator.

## Dependencies

- **Needs:** nothing. It should land after FEATURE-015, which changes the same function, and
    before FEATURE-022, which cuts `rebuild.py` into modules; new code goes into new modules.
- **Unblocks:** the first dataset that pins Silver; FEATURE-024, whose erasure path needs an
    expiry that deletes files; FEATURE-023, whose lease replaces the hand-disabled trigger of 4.3.

## Open assumptions

- **Assumption:** Athena shows the summary of a snapshot, with properties PyIceberg wrote, in
    `"landing_ingest$snapshots"`. — **Evidence:** the quality queries already read
    `"listing$snapshots"`; the `summary` column is unvalidated. — **If wrong:** Athena readers
    take the ids from `list`; PyIceberg readers are unaffected.
- **Assumption:** Athena's `VACUUM` deletes the files of snapshots PyIceberg expired, and expires
    nothing itself when the table's maximum snapshot age is set far above the policy. —
    **Evidence:** unvalidated; task 4.2 tries it on a scratch table. — **If wrong:** no `VACUUM`
    on Silver. Expiry stays metadata-only, files stay, and file removal becomes its own plan.

## Design

- **Primary approach:**
    - The manifest is the summary of the ingest log's commit. Before `record_ingested`, the run
        reads the current snapshot id of every table of the namespace except the log, and passes
        `silver_snapshot.<table> = <id>` with the run's properties (`rebuild.py:328-339`) to the
        log's write. The checkpoint and the consistency point are one atomic Iceberg commit, and
        the id of that log snapshot is the single reference a dataset records. Every table is
        named, also one the run did not write.
    - Readers: `current_manifest`, `manifest(id)` and `manifests` in `src/silver/manifest.py`
        return a frozen `RunManifest` (its id, when the run started, table to snapshot id).
    - `rollback` checks that every named snapshot still exists, then sets each table and, last,
        the log to the manifest's snapshots with `set_current_snapshot`. The runs after it are
        pending again.
    - `pin` tags the log and every named table with one name, so any engine's expiry keeps them.
    - `expire --keep-days <n>` keeps the newest manifest, pinned manifests and those whose run
        started less than `n` days ago; in each table it keeps what those name and what a
        reference points to, and expires the rest by id. Without `--apply` it only reports.
    - Files: metadata files are bounded by the table property, set at creation and by `expire`
        on older tables. Data files of expired snapshots and orphans are removed by an operator's
        Athena `VACUUM`; small files of `listing` and `listing_text` by `OPTIMIZE`.
- **Contracts and invariants:**
    - One manifest per run that processed something: the log snapshot that is current after the
        commit. The delete an upsert writes before its append is never a manifest.
    - A run with nothing pending, a refused run and a crashed run leave the last manifest
        standing, and every snapshot it names stays readable.
    - A manifest's age is its `rebuild_started_at`, the run's injected clock.
    - No row of any table changes: no new `transformation_version`, no backfill. The first
        manifest is written by the first run that processes something after the deployment.
    - No other engine rewrites `landing_ingest`: its current snapshot is the manifest. Rollback
        and expiry run only while no Silver run does.
- **OOP / SOLID:** pure functions over a catalog and one frozen value object; a `ManifestError`
    for a missing manifest or a named snapshot that is gone.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - a manifest table (a second commit after the checkpoint) or manifest columns in the log
        (the sink adds no column to an upserted table, `src/silver/sink.py:112-113`);
    - a tag per run on every table: 14 more commits per run;
    - age-based expiry alone: it would expire a snapshot a younger manifest still names;
    - an orphan-file remover of our own on an unversioned bucket, and the catalog's managed
        table optimizers, billed per DPU-hour for 14 tables.

## Approach

### Phase 1 — The manifest

- [ ] **1.1 A run writes its manifest.** RED, `tests/test_silver_manifest.py`: after a run over
    the synthetic landing, `current_manifest` names the current snapshot of every table but the
    log; a run that affects no listing still names `listing`; a run with nothing pending adds
    none; a backfill adds one, not two; after a run refused by a drifted later table, the
    manifest is the earlier one and each snapshot it names scans to the earlier rows. Fails:
    the module does not exist. GREEN: `src/silver/manifest.py`, and `record_ingested` takes the
    commit's properties.
- [ ] **1.2 Read and list.** RED: after three runs `manifests` returns three in order, and
    `manifest(first)` scans to the rows captured after run one; an unknown id, and a log that
    predates the feature, raise `ManifestError`. Fails: the functions are missing.
- [ ] **1.3 Report.** RED, `tests/test_silver_cli.py`: the run prints `manifest: snapshot <id>`
    after the table lines. Fails: no such line. GREEN: `ProcessResult.manifest_snapshot_id`.

### Phase 2 — Rollback and pin

- [ ] **2.1 Rollback.** RED, `tests/test_silver_maintenance.py`: after three runs,
    `rollback(first)` gives every table the rows of run one and a log without runs two and three;
    the next run processes both and ends in the rows before the rollback; a manifest naming an
    expired snapshot is refused and no table changes; a table the manifest does not name is left
    and reported. Fails: no function.
- [ ] **2.2 Pin.** RED: `pin(id, name)` puts the tag on the log and on every named table at the
    named ids; the same call again changes nothing; the same name for another manifest is
    refused. Fails: no function.

### Phase 3 — Expiry

- [ ] **3.1 Expire.** RED, with one clock value per run: the newest, the pinned and the young
    manifests stay and each snapshot they name scans; every other snapshot is gone, the
    intermediate deletes included; a table head no manifest names stays; without `apply` nothing
    changes and the report is the same; a second call expires nothing; a namespace without a
    manifest is refused. Fails: no function. GREEN: the kept set, then `by_ids` per table.
- [ ] **3.2 Metadata files.** RED, `tests/test_silver_sink.py`: a table each writer creates
    carries `write.metadata.delete-after-commit.enabled`; `expire` sets it on a table without it.
    Fails: tables carry no property.
- [ ] **3.3 Command.** RED, `tests/test_silver_maintenance_cli.py`: `python -m
    src.silver.maintenance list|rollback|pin|expire --catalog <name>` prints ids and counts only;
    exit 0, 1 for a `ManifestError`, 2 for usage. Fails: no module.

### Phase 4 — Operation

- [ ] **4.1 Documentation.** The runbook: the corrected sentence about refused runs, the manifest,
    reading through it with PyIceberg and Athena, rollback and maintenance by command, the policy
    and its cost. ADR 0001: a dated amendment (one manifest id per dataset, the policy).
    `analysis/silver-quality/README.md`: the set comes from one manifest. `README.md` and
    `docs/architecture.html`: the two limits become what is built. The quality report: one
    sentence once its set is expired. Validation: the leak guard, the workflow validator, and no
    `rollback_to(` left under `docs/`.
- [ ] **4.2 Scratch check (operator, with approval, at most $0.05).** In the Silver database,
    write a scratch table with PyIceberg, tag one snapshot, expire the others, raise the maximum
    snapshot age, run `VACUUM`. Record: the tagged snapshot scans, the expired files are gone,
    `$snapshots` shows a written property. Drop the table.
- [ ] **4.3 Deployment and first maintenance (operator, with approval, at most $0.10).** Deploy
    the image; note the first manifest id. With the trigger disabled as for a backfill: `expire`
    as a report, then with `--apply`; `list`. If 4.2 passed and after the owner's separate
    approval: `VACUUM` per table, and `OPTIMIZE` where the report shows many files. Record
    snapshots, objects and bytes before and after, as counts, in
    `dev/plans/implementations/FEATURE-020-run-manifest.md`.

## Files

- **Create:** `src/silver/manifest.py` (write and read a manifest), `src/silver/maintenance.py`
    (rollback, pin, expire and the command).
- **Change:** `src/silver/rebuild.py` (manifest before the log, report), `src/silver/ingest.py`
    (the commit's properties), `src/silver/sink.py` (table property), `docs/silver-rebuild.md`,
    `docs/adr/0001-silver-on-iceberg.md`, `analysis/silver-quality/README.md`, `README.md`,
    `docs/architecture.html`, `docs/data-quality/silver-quality-2026-11.md`.
- **Tests:** new `tests/test_silver_manifest.py`, `tests/test_silver_maintenance.py`,
    `tests/test_silver_maintenance_cli.py`; changed `tests/test_silver_cli.py`,
    `tests/test_silver_sink.py`, `tests/test_silver_ingest.py`.

## Test strategy

- **Unit:** the kept set of an expiry as a pure function of manifests, references and a date.
- **Contract / fixtures:** the summary keys of a manifest; the synthetic landing, no real id.
- **Integration:** a local SQLite catalog: runs, a refused run, rollback, pin, expiry.
- **Retries / idempotency / evolution:** a repeated rollback, pin and expiry; a log that predates
    the feature; a table created after the manifest; a backfill.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`.
- **Manual:** tasks 4.2 and 4.3.

## Operational and compliance impact

- **Data sourcing / legal:** nothing crosses the landing contract.
- **Privacy / security:** the first expiry removes every snapshot older than the first manifest
    from the catalog, the `r1` text included; its files go with `VACUUM`. No IAM change.
- **Observability:** one report line per run; `list` replaces the 30-day log as the record.
- **Failure and recovery:** an interrupted rollback or expiry is repeated. Until `VACUUM` the
    expired files and the previous metadata file still exist (a repair by hand, not rehearsed).
- **Migration / compatibility:** additive. Snapshots written before the feature belong to no
    manifest and expire in the first maintenance.

## Estimated monthly cloud cost

$0/month — no resources added or changed. Usage of existing services:

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| Athena `VACUUM`, `OPTIMIZE` | $5 per TB, 10 MB minimum per statement | 16 statements a month | under $0.01 |
| S3 storage of kept snapshots | $0.023 per GB-month | 8 runs a day, 10 MB rewritten per run, 30 days: 2.4 GB | ~$0.06 |
| **Total** | | | **under $0.10/month** |

- **Cost drivers & cheaper alternatives:** the days kept. Without expiry the same assumption adds
    about 29 GB a year, $0.67 a month after one year and rising. Cheaper: 14 days (about $0.03),
    or metadata-only expiry without Athena at $0, which leaves the files and the `r1` text.
- **External / non-AWS costs:** none.
- **Budget check:** yes.

## Success criteria

- [ ] Every run that processes something prints one manifest id that names every table's
    snapshot; a reader that follows it never sees tables of two runs, also after a refused run.
- [ ] One command rolls Silver back to a manifest; the runs after it are processed again.
- [ ] After the first maintenance only the snapshots of kept manifests and the table heads remain.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak and workflow checks pass
- [ ] The policy, the commands and their cost are in the runbook and in ADR 0001
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (owner):** how many days of manifests are kept? *Recommendation:* 30, the window in
    which a bad deployment is still found; 14 halves the storage.
- **Question (owner):** is the quality report's set of 2026-10-31 pinned before the first expiry?
    *Recommendation:* no. It holds `r1` text, privacy outranks reproducing a finished report, and
    its numbers are recorded; the report gains one sentence.
- **Question (owner):** who runs maintenance? *Recommendation:* the operator, monthly, by hand at
    $0, until FEATURE-023's lease makes a scheduled second writer safe.
- **Question (owner, in 4.3):** approve the deployment, the first expiry, and separately `VACUUM`.
- **Risk:** an expiry or `VACUUM` deletes history that cannot be rebuilt, or runs beside a
    Silver run. — *Mitigation:* report before `--apply`, the scratch check, `VACUUM` as its own
    approved step, the trigger disabled as for a backfill; the lease is FEATURE-023.
- **Risk:** the ingest log gains one small file per run and is scanned twice per run. —
    *Mitigation:* a backfill rewrites it; task 4.3 records the count for a later decision.

## Progress log

- **2026-11-22** — Plan created from review items B1 and B4; both hold in the code. Added: the
    runbook's rollback call does not exist in PyIceberg 0.12.0, and its expiry deletes no file.
