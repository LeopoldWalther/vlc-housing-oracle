# Landing contract v1

`landing/v1/` is where data acquisition ends and this repository begins. It is four Parquet tables,
`landing/v1/<table>.parquet`, written by a private acquisition repository and only read here.
[`schema.json`](schema.json) lists every column and its DuckDB type, in order. This file explains
what the rows mean.

Every rule that needs to know how the source portal or the collector behaves is applied before
landing. Everything that does not need that knowledge is left to Silver.

## Identity of the contract

- `schema.json` is a byte-for-byte copy of the acquisition repository's file. Both repositories pin
    its SHA-256 in a test (`tests/test_landing_contract.py`); the current digest is
    `f52989ab75f2321393082ea882a2b88c5630b51d22c71ff8af44335092369b72`.
- A table conforms when `DESCRIBE SELECT * FROM read_parquet(<file>)` returns the same
    `(column_name, column_type)` list, in the same order.
- **v1 is never edited.** A change of meaning or columns is `landing/v2/`, with its own
    `schema.json` and digest, written beside v1 until every reader has moved.
- No column holds a URL.

## Shared columns

- `source`: which collection produced the row; a configured string.
- `area`: `concat_ws('-', municipality, district, operation)`, for example `<municipality>-rent`
    or `<municipality>-<district>-sale`. An observation's `area` equals the `area` of the walk that
    read it, which is how observations join walks.
- `observed_date`: the UTC day the collector assigned to the record.
- All instants are `TIMESTAMP WITH TIME ZONE` in UTC, and money is `INTEGER` euros.

## Tables

### `walk`: one row per run report

A collection run reads several areas one after another. Each area it reads is one walk and one row.

- `run_started_at` is the **run's** start, shared by every area of that run, not the area's own
    start.
- `run_ended_at` is `run_started_at` plus the summed `duration_seconds` of every report with the
    same start: the end of the whole run. Use it to decide whether a walk lies inside one UTC day.
- Both are null for the oldest reports, which recorded no start.
- `complete`: nothing stopped the walk early, and the pages read can hold the counted results. Null
    on the oldest reports.
- `is_replay`: true when the run re-derived stored pages instead of observing the portal. A replay's
    report carries the replay's date while its observations keep their capture date, so **a replay
    is never evidence that a listing disappeared**.
    - `is_replay_source = 'recorded'` when the report states it itself.
    - `is_replay_source = 'inferred'` when it was derived for older reports.
    - `is_replay` can be null only on reports that are never `complete`.
- `pages`, `result_count` (the portal's own count) and `duration_seconds` are carried as reported.

### `observation`: one row per search-result card

- **Not deduplicated.** The same `source_listing_id` on the same day can appear in two areas,
    because the portal pads a result page with listings from neighbouring areas. Choosing one copy
    is Silver's rule.
- `is_empty_card`: the card carries no price, no size, no rooms and no freshness label. Such a card
    is padding and does not make a listing part of that area's stock. It is flagged, never dropped.

### `listing_detail`: one row per detail record

- Every typed attribute extracted from a listing's detail page. The free text is in `listing_text`.
- Not deduplicated: a listing can hold detail records in several areas and on several days.
- `has_<amenity>_state` (elevator, balcony, terrace, pool, garden, storage room, air conditioning,
    fitted wardrobes) is one of:
    - `'true'` when the page lists the amenity;
    - `'false_stated'` when the page explicitly denies it;
    - `'unknown'` when the page says nothing.
- `has_parking` and `has_heating` stay booleans.

### `listing_text`: one row per detail record, the free text

- `title`, `location`, `description` and `features` (the bullet list), all verbatim.
- **Unredacted.** Landing stays private. Silver redacts personal data before anything enters an
    analytics table.
- Joins `listing_detail` one-to-one on `(area, source_listing_id, observed_at)`.

## What landing deliberately does not do

- Deduplicate, choose a listing's newest record, or count days observed.
- Derive `disappeared` or `reappeared` events.
- Redact personal data.
- Merge listings that may be the same property.

All of these are pure functions of landing, and they belong to Silver.
