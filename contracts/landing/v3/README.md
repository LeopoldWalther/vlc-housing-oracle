# Landing contract v3

**Status:** proposed by [ADR 0003](../../../docs/adr/0003-immutable-bronze-and-landing-per-run.md),
which supersedes v2. It is frozen once both repositories pin the digest below: this repository in
its incremental-Silver feature, the acquisition repository in its FEATURE-016. Afterwards a change
is `landing/v4`.

Landing v3 carries **the same four tables as [v1](../v1/README.md), with one added column**:
`run_id`, the collection run a row came from, right after `source`. Read the v1 document for what
each row means. What v3 changes is the unit of work: **one immutable partition per collection run**,
announced by **one immutable commit per run**, so a reader finds its work by a set difference over
run ids and never by a clock.

## Identity of the contract

- [`schema.json`](schema.json) declares the layout, the commit fields and every table's columns.
    Its SHA-256 is `0ccda5843a11ec12b6d6ecf8cb9f8301c0df41a3564122b4c4bd78ce0672e61b`.
- A table file conforms when `DESCRIBE SELECT * FROM read_parquet(<file>, hive_partitioning = false)`
    returns the table's `(column_name, column_type)` list, in order.
- v3 never changes. A change of meaning or columns is `landing/v4` beside it.

## Layout

```
landing/v3/
├── walk/run_date=2026-10-20/run=20261020T030000Z/part-0.parquet
├── observation/run_date=2026-10-20/run=20261020T030000Z/part-0.parquet
├── listing_detail/run_date=…/run=…/part-0.parquet
├── listing_text/run_date=…/run=…/part-0.parquet
└── _commits/20261020T030000Z.json
```

- A **collection run** is one invocation of the acquisition job. Its `run_id` is its start instant
    as `YYYYMMDDTHHMMSSZ`; an import of earlier data uses `import-<YYYYMMDD>`. `run_date` is the
    run's UTC start date. A run's cards can be dated the next day; the row's own `observed_date`
    is authoritative, and the path only addresses the run.
- **Exactly one file per table and run,** always `part-0.parquet`, written once. A run that has no
    rows for a table holds a zero-row file, so every table has every run.
- Nothing under `landing/v3/` is ever rewritten or deleted.

## Commits: the change log

After a landing run has written a collection run's four files, it writes one commit for that run.
Commits are **never modified or deleted**.

```json
{
  "contract": "landing",
  "version": "v3",
  "schema_sha256": "0ccda5843a11ec12b6d6ecf8cb9f8301c0df41a3564122b4c4bd78ce0672e61b",
  "run_id": "20261020T030000Z",
  "run_started_at": "2026-10-20T03:00:00Z",
  "outcome": "ok",
  "is_replay": false,
  "row_counts": {"walk": 12, "observation": 1534, "listing_detail": 41, "listing_text": 41},
  "observed_dates": {
    "walk": ["2026-10-20"],
    "observation": ["2026-10-20"],
    "listing_detail": ["2026-10-20"],
    "listing_text": ["2026-10-20"]
  },
  "written_at": "2026-10-20T05:50:12Z"
}
```

- `outcome` is `ok` or `failed`: whether the collection run ended normally. A failed run's rows
    are still complete pages, and they are landed; the flag is for readers that want to know.
- `is_replay`: the run re-derived stored payloads instead of observing. Its rows carry the
    payloads' original capture instants, so a replay is never a walk for disappearance.
- `row_counts` holds each file's row count at the moment it was written.
- `observed_dates` lists, per table, the days this run's rows touch. A day-partitioned reader
    rewrites exactly those days.
- A writer may add fields; readers ignore fields they do not know.

## How a reader processes landing v3

1. Keep an **ingest log**: the set of `run_id`s already processed, stored with the reader's own
    output. Silver keeps it as an Iceberg table written last in the same run.
2. List `landing/v3/_commits/`. Pending runs are the commits whose `run_id` is not in the ingest
    log. **Order does not matter**, and processing a run twice must give the same result.
3. A day-partitioned reader takes the union of `observed_dates` over the pending commits and
    rewrites those days from **all** of landing v3, because several runs can touch one day.
4. Write the output, then add the pending `run_id`s to the ingest log. A crash in between means
    the runs are processed again, which is harmless by rule 2.

Missing a trigger costs nothing: the next invocation finds every pending run.

## What the writer guarantees

- A commit exists only after all four of its run's files are written, and every file it names is
    complete: each file is one atomic PUT.
- A collection run is landed exactly once, whatever started the landing job and however many
    landing jobs ran at the same time.
- Within one run, the `listing_detail`/`listing_text` pairing and the v1 column semantics hold.
    Across runs nothing is deduplicated: that is the reader's rule.
