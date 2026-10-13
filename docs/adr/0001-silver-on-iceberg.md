# ADR 0001 — Silver on Apache Iceberg, Gold on plain Parquet

**Status:** Accepted · **Date:** 2026-10-13

## Context

Silver is rebuilt whole from the landing contract by a DuckDB transformation. The corpus is small:
tens of thousands of listings, a few megabytes of Parquet, a rebuild in well under a second. On
size alone, versioned Parquet prefixes (`silver/v1/`) would be enough, and an earlier decision in
the acquisition repository chose exactly that.

What size does not cover is **reproducibility for models**. Silver is the input of every training
dataset. A model trained in March must be explainable in June, against the exact rows it saw, and
a whole-rebuild layout overwrites those rows on every run.

## Decision

- Silver tables are Apache Iceberg tables. The transformation stays DuckDB SQL; its result is
    written through PyIceberg as an `overwrite`, so **each rebuild is one snapshot**.
- A training dataset records the Silver `snapshot_id` it was built from. Time travel to that
    snapshot reproduces its input exactly.
- Schema changes inside a Silver version use Iceberg schema evolution. A change of meaning is still
    a new `transformation_version`, recorded per row.
- Gold stays plain Parquet under versioned prefixes, because the web front end reads it with
    DuckDB-WASM over HTTP and needs no catalog.

## Consequences

- One new dependency (`pyiceberg`) and a catalog. The catalog is chosen in the Silver feature plan
    (a local SQL catalog for tests; a managed catalog for the operator's runs).
- Snapshot expiry becomes an operational duty: snapshots referenced by a training dataset are kept,
    the rest expire on a documented policy.
- The sink is the one place where variation is real (Iceberg for Silver, Parquet for Gold), so it
    is the one place an interface is justified.

## Alternatives considered

- **Parquet under `silver/v<n>/`.** Simplest, and enough for the data volume. Rejected because
    pinning a training input would then mean copying the whole prefix per model.
- **Delta Lake.** Equivalent snapshot semantics for this use. Iceberg was preferred for its open
    catalog specification and the breadth of engines that read it (DuckDB, Athena, Snowflake,
    Spark), which keeps the choice of a future query engine open.
