# VLC Housing Oracle

An analytics platform for the Valencia housing market. Listing snapshots arrive through a
documented landing contract. From there, this repository builds historized Silver tables on Apache
Iceberg, Gold features and aggregates as Parquet, and models on top: fair-price estimation,
time-on-market, and market dynamics.

> **Status:** Silver is implemented: `python -m src.silver.rebuild --landing <dir> --catalog <name>`
> rebuilds four Iceberg tables from landing v1 ([runbook](docs/silver-rebuild.md)). Gold and models
> are not built yet.

## Architecture

```
 private acquisition            this repository
 ───────────────────            ───────────────────────────────────────────────────────────
 collection → Bronze  ──►  landing/v1/ (Parquet)  ──►  Silver (Iceberg)  ──►  Gold (Parquet)  ──►  notebooks · models · web
                           contract: contracts/landing/v1/schema.json
```

- **Landing** is the boundary. Its schema is a file, pinned by its SHA-256 in tests on both sides,
    and a change is a new version beside the old one.
- **Silver** is rebuilt whole from landing by DuckDB SQL. Each rebuild is one Iceberg snapshot, so
    a training dataset can name the exact rows it was built from
    ([ADR 0001](docs/adr/0001-silver-on-iceberg.md)).
- **Gold** is plain Parquet, readable by a browser over HTTP.

## Where the data comes from

Listings are collected privately from a public Spanish property portal. Acquisition is
deliberately kept out of this repository. So are all real data: tests and examples run on a
seeded synthetic landing dataset that reproduces the hard cases (replayed collection runs, runs
across midnight, padding cards, duplicate sightings).

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

python -m unittest discover -s tests -t .
ruff check src tests dev && ruff format --check src tests dev
python dev/tools/validate_workflow.py
```

## License

MIT, see [LICENSE](LICENSE).
