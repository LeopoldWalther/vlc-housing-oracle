# Silver rebuild

Silver is rebuilt whole from `landing/v1/` by one command. DuckDB SQL computes four tables, and
PyIceberg commits each one as an Apache Iceberg overwrite, so every rebuild leaves one snapshot per
table that a training dataset can pin ([ADR 0001](adr/0001-silver-on-iceberg.md)).

Real landing data and real Silver tables live only in the operator's private bucket and on the
operator's machine. Every value in angle brackets below is a placeholder; never commit the real
one.

## One-time setup

### 1. The Glue database

`infra/silver/` creates one AWS Glue database, `vlc_housing_silver`, whose location is the
`silver/` prefix of the existing private bucket that holds landing. Tables are created by the
rebuild, not by Terraform.

```bash
cd infra/silver
cp backend.hcl.example backend.tfvars          # fill in the state bucket; git ignores *.tfvars
echo 'lake_bucket = "<lake-bucket>"' > terraform.tfvars
terraform init -backend-config=backend.tfvars
terraform plan                                  # expect exactly one resource to add
terraform apply
```

Expected cost is about $0.02/month: Glue Data Catalog storage and requests stay inside the free tier
(one database, four tables; PyIceberg skips Glue's table-version archive by default), and the data
files are a few MB per rebuild in the existing bucket.

### 2. The catalog entry

PyIceberg resolves a catalog by name from `~/.pyiceberg.yaml` (or from
`PYICEBERG_CATALOG__<NAME>__<PROPERTY>` environment variables). Add an entry outside the
repository:

```yaml
catalog:
  silver:
    type: glue
    glue.region: eu-central-1
    glue.profile-name: <aws-profile>   # omit to use the default credential chain
```

No warehouse is needed: the Glue database's location decides where table files go.

The same entry as environment variables, for a single run. A dot in a property name is written as
`__`, because a shell variable name cannot contain a dot:

```bash
PYICEBERG_CATALOG__SILVER__TYPE=glue PYICEBERG_CATALOG__SILVER__GLUE__REGION=eu-central-1 \
    python -m src.silver.rebuild --landing ./landing/v1 --catalog silver
```

For a local trial without AWS, a SQLite catalog works the same way:

```yaml
catalog:
  local:
    type: sql
    uri: sqlite:////<absolute-path>/silver/catalog.db
    warehouse: file:///<absolute-path>/silver
```

## Running a rebuild

```bash
aws s3 sync s3://<lake-bucket>/landing/v1/ ./landing/v1/ --delete
python -m src.silver.rebuild --landing ./landing/v1 --catalog silver
```

The command prints one line per table, for example `listing: 14253 rows, snapshot 5435879517269196769`.
The first rebuild into new, empty tables also prints PyIceberg's `UserWarning: Delete operation did
not match any records`; it is harmless, because there was nothing to overwrite yet.

| Exit code | Meaning |
| --- | --- |
| 0 | All four tables committed. |
| 1 | Refused before any commit: landing breaks the contract (`LandingContractError`), or a table's columns no longer match its Iceberg schema (`SilverSchemaError`). The message names the table and a count, never a value. |
| 2 | Usage error, for example a `--landing` path that is not a directory. |

To try it on synthetic data instead:

```bash
python -c "from pathlib import Path; from src.synthetic.landing import generate_landing; p = Path('/tmp/landing'); p.mkdir(exist_ok=True); generate_landing(p, seed=1)"
python -m src.silver.rebuild --landing /tmp/landing --catalog local
```

## What the rebuild checks first

Before computing anything, every `landing/v1/<table>.parquet` must:

- exist, and match `contracts/landing/v1/schema.json` column for column (name and type, in order);
- have no nulls in the columns Silver's keys depend on (`source`, `area`, `observed_date` everywhere;
    `source_listing_id` and `observed_at` on the listing tables; `is_empty_card` on `observation`);
- have unique keys per area: `observation` on `(source, area, source_listing_id, observed_date)`,
    `listing_detail` and `listing_text` on `(source, area, source_listing_id, observed_at)`;
- pair every `listing_detail` row with exactly one `listing_text` row.

Every Arrow table is computed before the first commit, so a refused landing writes nothing.

## The tables

All four live in the namespace `vlc_housing_silver`. Every row carries `source` and
`transformation_version = 'silver-v1'`; all instants are UTC and money is integer euros.

### `walk` — one row per run report

Every landing `walk` column, unchanged. Silver does not re-derive `complete` or `is_replay`.
`run_started_at` is the run's start, and `run_ended_at` is the end of the whole run. A replay
(`is_replay`) is never evidence that a listing disappeared; that rule matters to `listing_event`
(FEATURE-002).

### `observation` — one row per listing and day

Landing repeats a listing-day when the portal pads a result page with neighbouring areas' listings.
Silver keeps one copy per `(source, source_listing_id, observed_date)`, chosen by a total order:

1. a real card before an empty (padding) card;
2. the higher `price_eur`, nulls last;
3. `municipality`, then `district` (nulls first), then `operation`, alphabetically;
4. `observed_at`, then `ingested_at` (nulls last).

Empty cards are kept and flagged (`is_empty_card`), never dropped.

### `listing` — one row per listing, attributes only

- Every `listing_detail` column of the listing's newest detail record. Newest means `observed_at`
    descending, then `ingested_at` descending, then `municipality`, `district`, `operation`. The
    record's `observed_at` is renamed `detail_observed_at`.
- `first_seen`, `last_seen` and `days_observed` over every Silver `observation` row of the
    listing, padding included.
- A listing seen only in search results has null attributes and null area columns; take its area
    from `observation`. A listing with a detail record but no search result has null seen columns.
- No free text.

### `listing_text` — one row per listing with a detail record

The free text of the same record `listing` chose: `title`, `location` and `features` verbatim, and
`description` redacted under `redaction_version = 'r1'`:

- Spanish phone numbers, with or without `+34` / `0034`, become `[phone]`;
- `Sr`, `Sra`, `Srta`, `Don`, `Doña` or `Dña` followed by a capitalised word becomes `[name]`.

A pattern change is a new redaction version (`r2`), recorded per row, never an edit of `r1`.

## Snapshots

Each commit's snapshot summary records what built it:

| Property | Example |
| --- | --- |
| `transformation_version` | `silver-v1` |
| `redaction_version` | `r1` |
| `landing_contract_sha256` | the digest of `contracts/landing/v1/schema.json` |
| `rebuild_started_at` | `2026-10-15T06:00:00+00:00` |

**Pin the snapshot id the command prints.** An Iceberg overwrite writes two snapshots: an empty
`DELETE`, then the `APPEND` holding the rows. The printed id is the table's current snapshot after
the commit — the `APPEND`. Pinning the `DELETE` would pin an empty table.

**Partial rebuilds.** The four commits are separate. If a rebuild fails between them, the tables'
current snapshots carry different `rebuild_started_at` values. Rerun the rebuild: it is idempotent
on row sets.

### Reading a pinned snapshot

PyIceberg:

```python
from pyiceberg.catalog import load_catalog

table = load_catalog("silver").load_table("vlc_housing_silver.listing")
listing = table.scan(snapshot_id=<snapshot-id>).to_arrow()
```

DuckDB, through the table's metadata file:

```sql
INSTALL iceberg;
LOAD iceberg;
SELECT * FROM iceberg_scan('<metadata-location>', snapshot_from_id => <snapshot-id>);
```

`table.metadata_location` in PyIceberg gives the metadata path.

### Rolling back

```python
table = load_catalog("silver").load_table("vlc_housing_silver.listing")
table.manage_snapshots().rollback_to(<snapshot-id>).commit()
```

Snapshots are not expired yet: nothing references one, and a rebuild adds a few MB. The first
feature that pins a snapshot for a training dataset sets the expiry policy.
