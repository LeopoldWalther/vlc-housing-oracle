# Silver runbook

Silver follows landing v3 incrementally. Every collection run that lands writes an immutable commit
under `landing/v3/_commits/`; Silver processes the commits its own ingest log does not hold yet,
replaces the day partitions they touch, recomputes the listings those days affect and writes the
ingest log last ([ADR 0001](adr/0001-silver-on-iceberg.md),
[ADR 0003](adr/0003-immutable-bronze-and-landing-per-run.md)). In AWS a landing commit queues a
run, and a Fargate task does the work; the same command runs on a laptop.

Real landing data and real Silver tables live only in the operator's private bucket and on the
operator's machine. Every value in angle brackets below is a placeholder; never commit the real
one.

## The command

```bash
python -m src.silver.rebuild --landing <dir-or-s3://lake-bucket/landing/v3> --catalog <name> \
    [--mode incremental|backfill] [--metrics]
```

| Mode | What it does |
| --- | --- |
| `incremental` (default) | Pending runs = landing's commits minus the ingest log. Rebuilds every day those runs touch from every run that touches it, upserts the affected rows of `listing` and `listing_text`, then records the runs in the ingest log. Nothing pending prints `nothing pending` and writes nothing. |
| `backfill` | Treats every commit as pending and rewrites all tables whole. This is the only path that may add a new column to an existing table, and the way a new `transformation_version` reaches history. |

`--metrics` publishes `PendingRuns` (runs still pending when the command ends: 0 on success) to
the CloudWatch namespace `vlc-housing-silver`; the AWS task always passes it.

A run prints, for example:

```
pending: 2
processed: <run-id>, <run-id>
walk: 1 days, snapshot <snapshot-id>
observation: 2 days, snapshot <snapshot-id>
listing_detail: 2 days, snapshot <snapshot-id>
affected listings: 431
listing: snapshot <snapshot-id>
listing_text: snapshot <snapshot-id>
```

| Exit code | Meaning |
| --- | --- |
| 0 | Every table and the ingest log committed, or nothing was pending. |
| 1 | Refused: landing breaks the contract (`LandingContractError`), or a table's columns no longer match its Iceberg schema (`SilverSchemaError`, for example an incremental run over tables a backfill has not migrated yet). The message names a table, a run and a count, never a value. |
| 2 | Usage error, for example a `--landing` path that is not a directory. |

The first run into new, empty tables also prints PyIceberg's `UserWarning: Delete operation did
not match any records`; it is harmless.

### On synthetic data

```bash
python -c "from pathlib import Path; from src.synthetic.landing import generate_landing_v3; p = Path('/tmp/landing-v3'); p.mkdir(exist_ok=True); generate_landing_v3(p, seed=1)"
python -m src.silver.rebuild --landing /tmp/landing-v3 --catalog local --mode backfill
python -m src.silver.rebuild --landing /tmp/landing-v3 --catalog local   # nothing pending
```

### Catalog entries

PyIceberg resolves a catalog by name from `~/.pyiceberg.yaml` or from
`PYICEBERG_CATALOG__<NAME>__<PROPERTY>` environment variables (a dot in a property is written as
`__`). Keep the file outside the repository:

```yaml
catalog:
  silver:
    type: glue
    glue.region: <region>
    glue.profile-name: <aws-profile>   # omit to use the default credential chain
  local:
    type: sql
    uri: sqlite:////<absolute-path>/silver/catalog.db
    warehouse: file:///<absolute-path>/silver
```

The Glue catalog needs no warehouse: the Glue database's location decides where table files go.

## What a run checks first

For every run it reads, before computing anything:

- each table's file matches `contracts/landing/v3/schema.json` column for column (name and type,
    in order);
- each file's row count equals the count its commit declares;
- no nulls in the columns Silver's keys depend on, and unique keys per area within a run;
- every `listing_detail` row pairs with exactly one `listing_text` row of the same run.

Every Arrow table is computed before the first commit, so a refused run writes nothing and stays
pending.

## The tables

All live in the namespace `vlc_housing_silver`. Every row carries `source` and
`transformation_version = 'silver-v1'`; all instants are UTC and money is integer euros. A
listing seen on one day by several runs keeps its newest sighting, ordered by the run's
`run_started_at` (never by the `run_id` string), and every row keeps the `run_id` it came from.

| Table | Partition | One row per | Notes |
| --- | --- | --- | --- |
| `walk` | `observed_date` | run report | Every landing `walk` column, unchanged. A replay (`is_replay`) is never evidence that a listing disappeared. |
| `observation` | `observed_date` | listing and day | Padding duplicates collapse by a total order: a real card before an empty card, the higher `price_eur`, then area columns, then `observed_at` and `ingested_at`. Empty cards are kept and flagged. |
| `listing_detail` | `observed_date` | detail record | The history a listing recompute reads, so a night never rereads all of landing. No free text. |
| `listing` | — | listing | Attributes of the newest detail record (`detail_observed_at`) plus `first_seen`, `last_seen`, `days_observed` over all observations. A listing seen only in search results has null attributes. |
| `listing_text` | — | listing with a detail record | The chosen record's `title`, `location`, `features`, and `description` redacted under `redaction_version = 'r1'` (Spanish phone numbers → `[phone]`, honorific plus capitalised word → `[name]`). A pattern change is `r2`, never an edit of `r1`. |
| `landing_ingest` | — | processed run | `run_id`, `run_started_at`, `outcome`, `is_replay`, `commit_written_at`, `ingested_at`, `transformation_version`. Written last, upserted by `run_id`: it is the checkpoint. |

## Running in AWS

`infra/silver/` defines everything; the account-specific values live in git-ignored `*.tfvars`.

```
landing commit (S3 Object Created, prefix landing/v3/_commits/)
   └─► EventBridge rule ─► SQS FIFO queue (one message group "silver")
daily 05:30 UTC Scheduler ─┘          │
                                      ▼
                      Lambda consumer (reserved concurrency 1)
                      adopt the running Silver task, or start one
                                      │ polls DescribeTasks every 15 s
                                      ▼
                      Fargate ARM64 task: --mode incremental --metrics
```

- **Why a queue.** A FIFO queue with one message group hands the consumer one batch at a time, so
    two Silver tasks never run at once; PyIceberg would otherwise rebase a conflicting commit
    rather than refuse it. A burst of commits costs at most a few tasks, never one per commit.
- **Why the body is never read.** The task finds its work by the set difference itself. A message
    only means "look now", so a lost, duplicated or extra message is harmless: at worst a task
    prints `nothing pending` and exits 0 within a minute.
- **Adopt or start.** Before starting, the consumer lists running tasks of the Silver family. A
    task that is already running (an operator's backfill, or one whose earlier invocation timed
    out) is waited for instead of joined. The consumer raises a minute before its 15-minute
    deadline; the batch becomes visible again after 16 minutes and the next invocation adopts the
    same task.
- **Catch-up.** The Scheduler queues one message a day at 05:30 UTC, so a missed event delays
    Silver by at most a day.
- **Image.** GitHub Actions builds the image on every push to `main` and publishes
    `ghcr.io/<owner>/vlc-housing-oracle-silver:<sha>`. A new image runs only after `silver_image`
    is set to its SHA tag and applied; `latest` is refused by validation.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `lake_bucket` | — | The private bucket holding landing and Silver. |
| `silver_image` | — | The image by commit SHA or digest. |
| `alert_email` | — | Receives failure mail; confirm the SNS subscription once. |
| `trigger_enabled` | `false` | Whether a landing commit queues a run. |
| `catch_up_enabled` | `false` | Whether the daily catch-up queues a run. |
| `consumer_enabled` | `true` | Whether the Lambda reads the queue. |
| `consumer_reserved_concurrency` | `1` | Caps only this function; `null` on an account whose concurrency limit is 10, where AWS refuses any reservation. |

### First deployment

```bash
cd infra/silver
terraform init -backend-config=backend.tfvars
terraform plan -out=tfplan        # both starters off
terraform apply tfplan
```

Confirm the SNS subscription mail, run one backfill (below), compare its counts with a local
backfill of the same landing, then set `trigger_enabled = true` and `catch_up_enabled = true` and
apply again.

### A manual backfill in AWS

Disable the event source mapping first, so no queued run starts while the backfill rewrites the
tables (a running backfill would be adopted, but a queued run arriving between two of its
commits must not start beside it):

```bash
N=vlc-housing-silver
UUID=$(aws lambda list-event-source-mappings --function-name $N-consumer \
    --query 'EventSourceMappings[0].UUID' --output text)
aws lambda update-event-source-mapping --uuid "$UUID" --no-enabled

SUBNETS=$(aws ec2 describe-subnets --filters Name=default-for-az,Values=true \
    --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$N \
    --query 'SecurityGroups[0].GroupId' --output text)
aws ecs run-task --cluster $N --task-definition $N --launch-type FARGATE \
    --started-by manual-backfill \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --overrides '{"containerOverrides":[{"name":"silver","command":["--landing","s3://<lake-bucket>/landing/v3","--catalog","silver","--mode","backfill","--metrics"]}]}'

# when the task stopped with exit code 0:
aws lambda update-event-source-mapping --uuid "$UUID" --enabled
```

Messages queued meanwhile stay in the queue and run once the mapping is enabled again.

### The equality check

After a backfill, sync the same landing and compare every table's row count, and the ingest log
against the commits, with a local backfill into a SQLite catalog. Sync right after the task
finishes, so both see the same commits. Compare counts only; never print rows of real data.

### Alarms and failure mail

| Signal | Fires when | First step |
| --- | --- | --- |
| Task-failed rule | A Silver task stopped with a non-zero exit code or without its container exiting. | Read `/ecs/vlc-housing-silver`. Exit 1 names the refused table and run. |
| `vlc-housing-silver-dead-letter` | A message reached the dead-letter queue: three starts in a row failed. | See below. |
| `vlc-housing-silver-pending-runs` | `PendingRuns` stayed above 0 for six hours. | Check the consumer log `/aws/lambda/vlc-housing-silver-consumer` and the last task log. |

### The dead-letter queue

The dead-letter queue is a circuit breaker, not a store of work: its messages carry nothing,
because the pending runs are still pending in landing. **Never redrive it.**

1. Read the last task's log and the consumer's log.
2. Fix the cause (a contract breach in landing, a schema change that needs a backfill, an image).
3. Purge the dead-letter queue:
    `aws sqs purge-queue --queue-url "$(aws sqs get-queue-url --queue-name vlc-housing-silver-dead-letter.fifo --query QueueUrl --output text)"`.
4. Queue one run, or wait for the next commit or the catch-up; it processes everything pending.

### Turning it off

Set `trigger_enabled = false` and `catch_up_enabled = false` and apply. Queued messages stay up
to four days; disable `consumer_enabled` as well to stop the consumer from reading them.

### Cost

About $0.37/month for the AWS components, dominated by Fargate minutes (1 vCPU, 2 GB, ARM64, a
few minutes a day) and the task's public IPv4 address. A NAT gateway (≈ $35/month) is avoided by
the public IP; the cheaper alternative is running the command on a laptop at $0, without the
automation. The full estimate is in the FEATURE-004 plan.

## Snapshots

Each commit's snapshot summary records what built it:

| Property | Example |
| --- | --- |
| `transformation_version` | `silver-v1` |
| `redaction_version` | `r1` |
| `landing_contract_sha256` | the digest of `contracts/landing/v3/schema.json` |
| `landing_run_count` | `2` |
| `landing_run_first`, `landing_run_last` | the smallest and largest run id processed |
| `rebuild_started_at` | `2026-10-15T06:00:00+00:00` |

The full list of processed runs lives in `landing_ingest`, not in every summary.

**Pin the snapshot id the command prints.** Replacing a day writes a delete and an append; the
printed id is the table's current snapshot after the commit. Pinning an intermediate snapshot
could pin a table without the day.

**Partial runs.** The tables commit one after another. A run that stops between them leaves its
runs out of the ingest log, so they stay pending, and the next run replaces the same days again:
repeating a run gives the same rows.

### Reading a pinned snapshot

PyIceberg:

```python
from pyiceberg.catalog import load_catalog

table = load_catalog("silver").load_table("vlc_housing_silver.listing")
listing = table.scan(snapshot_id=<snapshot-id>).to_arrow()
```

DuckDB, through the table's metadata file (`table.metadata_location` in PyIceberg):

```sql
INSTALL iceberg;
LOAD iceberg;
SELECT * FROM iceberg_scan('<metadata-location>', snapshot_from_id => <snapshot-id>);
```

### Rolling back

Disable the starters (or the event source mapping), then roll every table back to the snapshots of
the same earlier run, the ingest log included, so the runs after it become pending again:

```python
table = load_catalog("silver").load_table("vlc_housing_silver.listing")
table.manage_snapshots().rollback_to(<snapshot-id>).commit()
```

A whole-history rollback is simpler as a backfill. Snapshots are not expired yet; the first feature
that pins a snapshot for a training dataset sets the expiry policy.
