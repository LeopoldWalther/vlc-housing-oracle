# VLC Housing Oracle

An analytics platform for the Valencia housing market. Listing snapshots arrive through a
documented landing contract. From there, this repository builds historized Silver tables on Apache
Iceberg, Gold features and aggregates as Parquet, and models on top: fair-price estimation,
time-on-market, and market dynamics.

> **Status:** Silver is implemented and follows landing v3: a landing commit queues a run in AWS,
> and `python -m src.silver.rebuild --landing <dir-or-s3> --catalog <name>` processes the runs its
> ingest log does not hold yet into eleven Iceberg tables ([runbook](docs/silver-rebuild.md)).
> Among them are a price history per listing and three tables of inferred labels: quality flags,
> property groups that put listings which are certainly the same property into one group, and
> labels read from the portal's feature list (let with tenants, bare ownership, furnishing). A
> further table lists every feature wording with the column that carries it, so that a wording
> nobody has read yet raises an alarm. A run that is given a vintage of the official census
> sections writes a twelfth table, `listing_location`: the section, district and municipality of
> every listing's coordinates.
> Gold and models are not built yet.

## Architecture

```
 private acquisition            this repository
 ───────────────────            ───────────────────────────────────────────────────────────
 collection → Bronze  ──►  landing/v3/ (Parquet, one commit per run)  ──►  Silver (Iceberg)  ──►  Gold (Parquet)  ──►  notebooks · models · web
                           contract: contracts/landing/v3/schema.json
```

- **Landing** is the boundary. Its schema is a file, pinned by its SHA-256 in tests on both sides,
    and a change is a new version beside the old one.
- **Silver** is maintained incrementally from landing by DuckDB SQL: landing announces every
    collection run with an immutable commit, Silver processes the commits its own ingest log does
    not hold yet and replaces the days they touch, and every Iceberg snapshot is traceable to its
    input, so a training dataset can name the exact rows it was built from. A full rebuild remains
    as a backfill ([ADR 0001](docs/adr/0001-silver-on-iceberg.md),
    [ADR 0003](docs/adr/0003-immutable-bronze-and-landing-per-run.md)).
- **Reference** data is public data that Silver reads beside landing, first the boundaries of
    the census sections. It is stored as immutable vintages with a manifest that names source
    and licence; a command of this repository converts a file that an operator downloads, and no
    code fetches it ([ADR 0004](docs/adr/0004-reference-data.md)).
- **Gold** is plain Parquet, readable by a browser over HTTP, and holds use-case-specific
    datasets. It has none yet; labels that describe a listing as such, like quality flags and
    property groups, live in Silver.

## Where the data comes from

Listings are collected privately from a public Spanish property portal. Acquisition is
deliberately kept out of this repository. So are all real data: tests and examples run on a
seeded synthetic landing dataset that reproduces the hard cases (replayed collection runs, runs
across midnight, padding cards, duplicate sightings).

Census-section boundaries come from the Spanish national statistics office under CC BY 4.0
(*Elaboración propia con datos extraídos del sitio web del INE: www.ine.es*). The boundary file is
kept out of this repository as well; tests run on invented rectangles.

## How work happens here

Every non-trivial change goes through an **Architect → Reviewer → Implementer** workflow
([`.claude/WORKFLOW.md`](.claude/WORKFLOW.md)):

1. the Architect writes a feature plan with explicit assumptions, evidence and TDD slices
    (`dev/plans/`);
2. the Reviewer gates it against the repository and emits an executable technical plan
    (`dev/reviews/`, `dev/plans/technical/`);
3. the Implementer executes it one red-green-refactor task at a time.

`python dev/tools/validate_workflow.py` keeps plans, reviews and the feature registry consistent,
and CI runs it on every change.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -c "import duckdb; duckdb.sql('INSTALL spatial')"

python -m unittest discover -s tests -t .
ruff check src tests dev && ruff format --check src tests dev
python dev/tools/validate_workflow.py
```

## License

MIT, see [LICENSE](LICENSE).
