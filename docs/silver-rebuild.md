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
    [--mode incremental|backfill] [--metrics] [--census-sections <vintage-directory>] \
    [--text-labels <dir-or-s3://lake-bucket/enrichment/v1/text_label>]
```

| Mode | What it does |
| --- | --- |
| `incremental` (default) | Pending runs = landing's commits minus the ingest log. Rebuilds every day those runs touch from every run that touches it, upserts the affected rows of `listing` and `listing_text`, recomputes `listing_quality` and `listing_property_group` from Silver's whole `listing`, `feature_pattern` and `listing_feature_label` from Silver's whole `listing` and `listing_text`, `listing_event` from Silver's whole `walk` and `observation`, and `listing_price` from Silver's whole `observation` and `listing_detail`, and replaces all six. Given `--census-sections`, it also recomputes `listing_location` from Silver's whole `listing` and the vintage's polygons and replaces it. Given `--text-labels`, it also recomputes `listing_text_label` from Silver's whole `listing` and `listing_text` and the stored answers and replaces it. Then it records the runs in the ingest log. Nothing pending prints `nothing pending` and writes nothing. Before either, it refuses with exit code 3 when the ingest log holds another `transformation_version` or `listing_text` another redaction version than the code: run a backfill. |
| `backfill` | Treats every commit as pending and rewrites all tables whole. This is the only path that may add a new column to an existing table, and the way a new `transformation_version` reaches history. |

`--metrics` publishes `PendingRuns` (runs still pending when the command ends: 0 on success) to
the CloudWatch namespace `vlc-housing-silver`; the AWS task always passes it. A run that
processed something publishes `UnknownFeaturePatterns` in the same call: the feature wordings of
Silver that are not in the registry. A run with nothing pending computed nothing and publishes
`PendingRuns` alone.

`--census-sections` names one vintage directory of the census sections, local or `s3://`
([Reference data](#reference-data)). A run given it also writes `listing_location`; a run without
it neither reads reference data nor writes that table, and needs no spatial extension. The AWS
task passes it when the variable `census_sections` is set.

`--text-labels` names the text labeller's stored answers, local or `s3://`
([Text labels](#text-labels)). A run given it also writes `listing_text_label`, with null labels
while no answer is stored; a run without it neither reads the answers nor writes that table. The
AWS task always passes it.

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
listing_quality: snapshot <snapshot-id>
listing_event: snapshot <snapshot-id>
listing_property_group: snapshot <snapshot-id>
listing_price: snapshot <snapshot-id>
feature_pattern: snapshot <snapshot-id>
listing_feature_label: snapshot <snapshot-id>
listing_location: snapshot <snapshot-id>
listing_text_label: snapshot <snapshot-id>
unknown feature patterns: 0
```

The `listing_location` line appears only for a run given `--census-sections`, the
`listing_text_label` line only for a run given `--text-labels`.

| Exit code | Meaning |
| --- | --- |
| 0 | Every table and the ingest log committed, or nothing was pending. |
| 1 | Failed: an error the command does not handle, such as a read of S3 that failed for the network or a permission, ends with Python's traceback. In AWS the queue retries it, up to three starts. |
| 2 | Usage error, for example a `--landing` path that is not a directory. |
| 3 | Refused: landing breaks the contract (`LandingContractError`: a missing or non-Parquet file, other columns, another row count or other days than its commit names), or a table's columns no longer match its Iceberg schema (`SilverSchemaError`, for example an incremental run over tables a backfill has not migrated yet), or the census-section vintage is missing, is not Parquet or fails its check (`ReferenceDataError`), or a stored answer lacks a column (`StoredAnswerError`), or an incremental run meets tables of another rule or redaction version (`BackfillRequired`). One line on stderr, `Silver run refused: <message>`, names a table, a run, a count, days, versions or the vintage's path and its defect, never a value; when another error caused it, ` [cause: <type>]` follows, never that error's text. A refusal is not retried: the same input would be refused again. |

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

A run given `--census-sections` checks the vintage before its first write as well: `part-0.parquet`
in the directory is readable, has the six columns of a vintage and at least one row, and every
row carries the same vintage. Otherwise the run is refused with the path in the message, writes
nothing and stays pending.

## The tables

All live in the namespace `vlc_housing_silver`. Every row carries `source` and
`transformation_version = 'silver-v1'`; all instants are UTC and money is integer euros. A
listing seen on one day by several runs keeps its newest sighting, ordered by the run's
`run_started_at` (never by the `run_id` string), and every row keeps the `run_id` it came from.
`listing_location` exists only where a run was given `--census-sections`, `listing_text_label`
only where a run was given `--text-labels`.

| Table | Partition | One row per | Notes |
| --- | --- | --- | --- |
| `walk` | `observed_date` | run report | Every landing `walk` column, unchanged. A replay (`is_replay`) is never evidence that a listing disappeared. |
| `observation` | `observed_date` | listing and day | Padding duplicates collapse by a total order: a real card before an empty card, the higher `price_eur`, then area columns, then `observed_at` and `ingested_at`. Empty cards are kept and flagged. |
| `listing_detail` | `observed_date` | detail record | The history a listing recompute reads, so a night never rereads all of landing. No free text. |
| `listing` | — | listing | Attributes of the newest detail record (`detail_observed_at`) plus `first_seen`, `last_seen`, `days_observed` over all observations. A listing seen only in search results has null attributes. |
| `listing_text` | — | listing with a detail record | The chosen record's `title`, `location`, `features`, and `description` redacted under `redaction_version = 'r2'` (Spanish phone numbers, with or without `+34`, `0034` or a `34` glued to the number → `[phone]`; honorific plus a capitalised or all-capitals word → `[name]`). r2 added the glued `34` and the capitals to r1. A pattern change is `r3`, never an edit of `r2`. |
| `feature_pattern` | — | feature wording | Every wording of `listing_text.features` with the number of listings that carry it, the column or label that carries what it says, and the listings that lack that value. A null `target` is a wording nobody has read yet. Private. See below. |
| `listing_feature_label` | — | listing with a detail record | What the portal's features state: `is_tenant_occupied`, `is_bare_ownership`, `furnishing`. An inferred label. See below. |
| `listing_quality` | — | listing with a detail record | Twelve boolean flags, one per plausibility and location rule. A flag is an inferred label and changes no value of `listing`. See below. |
| `listing_property_group` | — | listing with a detail record | The `property_group_id` of the listings that are certainly the same property under rule `pg1`, with the group's size and completeness. An inferred label; no listing is dropped. See below. |
| `listing_event` | — | inferred event of a listing | `event_type` is `disappeared` or `reappeared`, with `event_date`, `area`, `run_id` and `last_seen_date`. See below. |
| `listing_price` | — | listing and price period | The prices a listing was seen at, in order, with the first and last sighting of each, the observed and the declared previous price, and `is_current` on the last period. See below. |
| `listing_location` | — | listing with a detail record | The census section, district and municipality the listing's coordinates lie in, as the statistics office codes them, with the `reference_vintage` of the boundaries. Null codes without coordinates or outside every polygon. An inferred label. See below. |
| `listing_text_label` | — | row of `listing_text` | `renovation_state` and `is_auction` as a language model read them from the redacted description, with `label_version`, `model_id` and `labeled_at`. Null until the text is labelled, and for every listing that is not a sale. An inferred label. See below. |
| `landing_ingest` | — | processed run | `run_id`, `run_started_at`, `outcome`, `is_replay`, `commit_written_at`, `ingested_at`, `transformation_version`. Written last, upserted by `run_id`: it is the checkpoint. |

### `listing_quality`

One flag per rule of the data-quality findings
([`docs/data-quality/silver-quality-2026-11.md`](data-quality/silver-quality-2026-11.md)). The
thresholds are `QUALITY_THRESHOLDS` in `src/silver/rebuild.py`; the rules are
`src/silver/sql/listing_quality.sql`. Join the table to `listing` on `source` and
`source_listing_id`.

| Flag | True when |
| --- | --- |
| `price_out_of_bounds` | `price_eur` below 10,000 or above 20,000,000 (sale); below 100 or above 30,000 (rent) |
| `sqm_built_below_min` | `sqm_built` below 10 |
| `sqm_built_above_max` | `sqm_built` above 2,000 |
| `price_per_sqm_below_min` | `price_eur` below 300 × `sqm_built` (sale) or 2 × `sqm_built` (rent) |
| `price_per_sqm_above_max` | `price_eur` above 15,000 × `sqm_built` (sale) or 60 × `sqm_built` (rent) |
| `sqm_usable_above_built` | `sqm_usable` above `sqm_built` |
| `rooms_above_max` | `rooms` above 12 |
| `bathrooms_above_rooms` | `bathrooms` above `rooms` + 2 |
| `year_built_out_of_range` | `year_built` below 1500, or above the UTC year of `detail_observed_at` plus one |
| `price_drop_above_max` | `price_eur` below half of `prev_price_eur` |
| `coordinates_missing` | `latitude` or `longitude` is missing |
| `far_from_municipality` | more than 10 km from the municipality's centre |

- **A flag means "check", not "wrong".** In the findings, 17 of the 22 sale listings with a low
  price per m² were houses with large plots. No row of any table is changed or dropped; a
  consumer decides which flags exclude a listing from its analysis.
- **True only on evidence.** A flag is true when its inputs are present and break the rule. A
  missing input gives false, never null. Missing coordinates have a flag of their own; a missing
  `rooms` is not a break.
- **Operations.** `rent` listings are judged at the rent thresholds, every other operation at
  the sale thresholds.
- **The year bound follows the observation,** not the clock of the run, so a backfill gives the
  same flags.
- **The centre** of a municipality is the median latitude and median longitude of its listings
  with a detail record and coordinates, both operations together; the distance is the haversine
  distance on a sphere of 6,371 km. The centre moves with the listings, and in a municipality
  with very few listings it can move noticeably.
- **Comparisons are exact.** Prices and areas are integers; price per m² is compared as a
  product, never as a division, and as a 64-bit product, so an absurd value is flagged and
  cannot fail the run.
- The table is recomputed from Silver's whole `listing` and replaced whole in every run that
  processes something, in both modes: one listing can move a centre and with it the flag of any
  other listing of its municipality.

### `listing_property_group`

Maps every listing with a detail record to a `property_group_id`, so that listings which are
certainly the same property, such as one flat advertised by several agencies, count once.
Counting distinct `property_group_id` counts properties. A group says "certainly the same
property under rule `pg1`", never sold, rented or re-listed. Join the table to `listing` on
`source` and `source_listing_id`.

Two listings with a detail record form a **pair** when all of these hold:

- equal `operation`, `municipality`, `rooms`, `bathrooms` and `sqm_built`, none of them missing;
- the higher `price_eur` is at most 5% above the lower, compared in integers
  (`100 × higher ≤ 105 × lower`), so exactly 5% pairs;
- both have coordinates and lie less than 350 m apart (haversine distance on a sphere of radius
  6,371 km);
- floor: if either listing's `condition` is `new`, both floors are present and equal; otherwise
  the floors are equal or at least one is missing. A missing `condition` counts as not new.

The source is not compared, so listings of two sources can pair, and so can a listing and its
later re-listing. A **group** is a connected component of the pairs: the pair rule is not
transitive, and a partition of the listings has to be. A listing in no pair is its own group of
one. The thresholds are named constants in `src/silver/property_group.py`; the pair rule is
`src/silver/sql/pairs.sql`. The evidence is the manual precision check of the data-quality
findings ([`docs/data-quality/silver-quality-2026-11.md`](data-quality/silver-quality-2026-11.md)):
29 of 29 judgeable pairs were the same property, and every relaxation tested was uncertain. A
change of the rule is a new `property_group_version`, never an edit of `pg1`.

| Column | Type | Meaning |
| --- | --- | --- |
| `source`, `source_listing_id` | `VARCHAR` | The listing |
| `property_group_id` | `VARCHAR` | `<source>:<source_listing_id>` of the group's smallest member, ordered by `(source, source_listing_id)` |
| `group_size` | `INTEGER` | Listings in the group; 1 for a listing without a duplicate |
| `group_is_complete` | `BOOLEAN` | Every two members satisfy the pair rule; false marks a group held together by a chain |
| `property_group_version` | `VARCHAR` | `pg1` |
| `transformation_version` | `VARCHAR` | As in every Silver table |

- **Stable within one snapshot only.** When a listing joins a group or two groups merge, the
  `property_group_id` can change with the next run. Join on it only within one snapshot of the
  table; a consumer that needs a fixed mapping pins the snapshot id the run printed.
- **Groups of more than two listings are not manually validated.** The precision check judged
  pairs, never groups. A large group may be a development whose units are not marked as new
  build. `group_size` and `group_is_complete` are on every row, so a consumer can cap the size
  it trusts or exclude incomplete groups.
- **Recall.** The rule is deliberately strict and misses roughly a third of true duplicates (see
  the findings), so supply counted on it is still overstated.
- **One-room homes cannot pair** while `rooms` is missing for them upstream: a missing value
  never matches.
- The table is recomputed from Silver's whole `listing` and replaced whole in every run that
  processes something, in both modes: a new listing can join an existing listing's group, and a
  changed one can leave it, so a run changes rows of listings it did not touch.

### `listing_event`

A listing disappeared on the first day after a sighting on which a complete live walk of its home
area did not see it; it reappeared on the day it was seen again after such a day. Evidence is a
walk with `complete` true, `is_replay` false (null is not false) and a start and end on the same
UTC date; the home area is the area of the listing's latest real card, since an empty card only
names a search that happened to show it.

- `event_date` of a `disappeared` row is when the absence was noticed, not when the listing left:
  it left somewhere between `last_seen_date` and `event_date`. `run_id` names the run of that day
  that started first.
- No event is not proof of presence. Without an evidence day nobody looked, and the listing may
  have left and come back unnoticed.
- Disappearance is observed absence, never a sale or a rental.
- A late run can retract an event: a sighting inside a gap closes it, and the next run removes
  the `disappeared` row that gap held.
- The table is recomputed from Silver's whole `walk` and `observation` and replaced whole in every
  run that processes something, in both modes, because any run can add or retract events of any
  listing. Reading those two tables whole takes a few seconds; the table is the one exception to
  replacing day partitions.

### `listing_price`

The price history of every listing, as observed. The rule is `src/silver/sql/listing_price.sql`.
Join the table to `listing` on `source` and `source_listing_id`.

- **A sighting** is a row of `observation` that is not an empty card and has a `price_eur`, or a
  row of `listing_detail` with a `price_eur`.
- **The order** of a listing's sightings is total: by `observed_at`, then cards before detail
  records (the detail page is fetched after the card that led to it), then by `price_eur`, then by
  `prev_price_eur` with nulls last. Sightings equal in all four are interchangeable for every
  column.
- **A period** is a maximal run of consecutive sightings with one price. A return to an earlier
  price is a new period, so prices 400,000, 380,000, 400,000 are three periods.

| Column | Type | Meaning |
| --- | --- | --- |
| `source`, `source_listing_id` | `VARCHAR` | The listing |
| `period` | `INTEGER` | 1 for the first observed price, rising by 1 |
| `price_eur` | `INTEGER` | The price of the period |
| `first_observed_at`, `last_observed_at` | `TIMESTAMP WITH TIME ZONE` | The period's first and last sighting |
| `sightings` | `INTEGER` | Sightings in the period |
| `previous_price_eur` | `INTEGER` | The price of the period before; null for period 1 |
| `declared_previous_price_eur` | `INTEGER` | The `prev_price_eur` of the period's first sighting, card or detail record; null without one |
| `is_current` | `BOOLEAN` | True for the listing's last period, never null |
| `transformation_version` | `VARCHAR` | As in every Silver table |

- **Which price is current.** `listing.price_eur` stays the price of the chosen detail record. The
  latest price seen is the row with `is_current` true, which may come from a later card.
- **`is_current` means the last price seen,** not that the listing is online. Presence is
  `listing_event`'s question.
- **Observed and declared are separate.** `previous_price_eur` is what this table saw;
  `declared_previous_price_eur` is the portal's statement, which may name a price this table never
  saw. Neither is a percentage; compute changes from the two integers.
- **Short-lived prices can be missed.** A period is what was observed; a price that came and went
  between two sightings leaves no row.
- The table is recomputed from Silver's whole `observation` and `listing_detail` and replaced whole
  in every run that processes something, in both modes: a late run can add a sighting inside any
  listing's history and split or join its periods.

### `feature_pattern`

The portal lists a listing's features as short strings. This table shows every wording of them,
so that a fact the portal states and no column holds is noticed in the next run. The rule is
`src/silver/sql/feature_pattern.sql`; the registry is `src/silver/feature_patterns.py`.

- **A wording** is a feature string with every number replaced by `#`, trimmed and lower-cased
  (`src/silver/sql/feature_wording.sql`): "80 m² construidos" and "125 m² construidos" are the
  wording `# m² construidos`. A number may carry thousands and decimal separators.
- **A target** is what carries the wording's meaning: a column of `listing`, one of the labels of
  `listing_feature_label`, or `unread` for a meaning that no column carries yet.

| Column | Type | Meaning |
| --- | --- | --- |
| `pattern` | `VARCHAR` | The wording |
| `listings` | `INTEGER` | Distinct listings whose features hold the wording |
| `target` | `VARCHAR` | The registry's target; null for a wording that is not in the registry |
| `listings_target_missing` | `INTEGER` | Of those listings, the ones whose target column is null, `unknown` or an empty list; null when the target is null, a label or `unread` |
| `transformation_version` | `VARCHAR` | As in every Silver table |

Rows are ordered by `listings` descending, then `pattern`.

- **A null `target` is work to do.** The run prints the count as `unknown feature patterns` and
  publishes it as `UnknownFeaturePatterns`; above zero the alarm mails. Read the rows with a
  null target, decide for each which column or label carries it, and add it to `KNOWN_PATTERNS`
  with that target, or with `unread` when nothing carries it. A registry change needs no
  `transformation_version` and no backfill: the next run that processes something recomputes
  both tables whole.
- **List a wording by digest when the leak guard refuses it.** A wording that contains a term
  the leak guard forbids is never written in clear: put the SHA-256 of its UTF-8 bytes into
  `KNOWN_PATTERN_DIGESTS` instead. Never change the leak guard to make a wording pass.
- **`listings_target_missing` above zero** means the portal states the fact for those listings
  and the column does not hold it. Its repair belongs to the acquisition side, which extracts
  the columns; this repository only reports the count. A listing with a text row and no row in
  `listing` counts as missing.
- **`unread` wordings** are meanings the landing contract has no column for. They are reported to
  the acquisition side with their counts.
- **The table is private.** A wording that is not in the registry is text nobody has read, and
  it could be typed by a person. Publish counts, and wordings of the registry only.

### `listing_feature_label`

Three labels that the features state and no column of `listing` holds. The rule is
`src/silver/sql/listing_feature_label.sql`. Join the table to `listing` on `source` and
`source_listing_id`; it has one row per row of `listing_text`.

| Column | Type | Meaning |
| --- | --- | --- |
| `source`, `source_listing_id` | `VARCHAR` | The listing |
| `is_tenant_occupied` | `BOOLEAN` | A feature says the home is let, with tenants; never null |
| `is_bare_ownership` | `BOOLEAN` | A feature says the sale is of the bare ownership; never null |
| `furnishing` | `VARCHAR` | `furnished`, `kitchen_only` or `unfurnished`; null when no feature states it or two features state different values |
| `transformation_version` | `VARCHAR` | As in every Silver table |

- **A label is the portal's statement,** not a verified fact. It is an inferred label beside the
  facts of `listing` and changes no value there.
- **False means "not stated".** A listing without the wording has false, also a listing whose
  feature list is empty.
- **The labels do not depend on the operation.** Filter by `listing.operation` when a question
  is about sales or about rentals only.
- Both feature tables are recomputed from Silver's whole `listing` and `listing_text` and
  replaced whole in every run that processes something, in both modes: a run can change any
  listing's features and with them the counts of any wording.

### `listing_location`

Where a listing lies in the official geography: its census section, and with the section its
district and municipality. The portal names a municipality and, inside the city only, a district
and a neighbourhood; these codes exist for every located listing and are the keys under which
public statistics are published. The rule is `src/silver/sql/listing_location.sql`; the polygons
are one vintage of the [reference data](#reference-data). Join the table to `listing` on `source`
and `source_listing_id`.

- **The rule.** A listing belongs to the polygon its point lies in or on the border of. Of
  several such polygons the one with the smallest section code is taken, and all three codes
  come from that one polygon, so every listing has exactly one row.
- **Longitude is x.** The polygons are stored in EPSG:4326 with longitude before latitude, and a
  listing's point is built as (longitude, latitude). Swapped axes would give null codes for
  every listing and no error, which is why the tests use rectangles that are wider than high.

| Column | Type | Meaning |
| --- | --- | --- |
| `source`, `source_listing_id` | `VARCHAR` | The listing |
| `municipality_code` | `VARCHAR` | 5 digits: province and municipality; null without coordinates or outside every polygon |
| `district_code` | `VARCHAR` | 7 digits: the municipality code and the district; null as `municipality_code` |
| `section_code` | `VARCHAR` | 10 digits: the district code and the section; null as `municipality_code` |
| `reference_vintage` | `VARCHAR` | The vintage of the boundaries the row was derived from; never null |
| `transformation_version` | `VARCHAR` | As in every Silver table |

- **The coordinate is approximate.** The portal places a listing near its address, not on it, so
  a listing can fall into a neighbouring section. Sections are small; statistics by district or
  municipality are safer than by section.
- **Null codes mean no evidence:** the listing has no coordinates, or its point lies outside
  every polygon of the vintage, which holds one province. They do not mean "outside the
  province".
- **The codes are the statistics office's.** They join its public tables by section, district or
  municipality. `municipality_code` can differ from what `listing.municipality` suggests: that
  column is the portal's name for the area the listing was found in.
- **`reference_vintage` names the input.** Boundaries change every year. A new vintage changes
  rows without a new `transformation_version`: the input changed, not the rule.
- The table is recomputed from Silver's whole `listing` and replaced whole in every run that
  processes something and is given `--census-sections`, in both modes. It is created by the
  first such run; no backfill is needed. A run without the argument leaves an existing table
  as it is, so the table goes stale while the argument is missing.

### `listing_text_label`

What the redacted description says about the home's state and whether it is sold at auction, as
a language model read it ([ADR 0005](adr/0005-model-derived-labels.md)). The rule is
`src/silver/sql/listing_text_label.sql`; the answers come from the [text labeller](#text-labels).
Join the table to `listing` on `source` and `source_listing_id`.

- **The rule.** A row of `listing_text` takes the newest stored answer of the current
  `label_version` for `sha256(description)`, and only when the listing is a sale and its
  description is not blank. Two listings with one description carry the same answer.

| Column | Type | Meaning |
| --- | --- | --- |
| `source`, `source_listing_id` | `VARCHAR` | The listing |
| `renovation_state` | `VARCHAR` | `needs_renovation`, `new_build`, `renovated`, `original` or `unknown`; null when not labelled or not a sale |
| `is_auction` | `BOOLEAN` | Whether the home is acquired through an auction; null as `renovation_state` |
| `label_version` | `VARCHAR` | The prompt, schema and model the answer came from; null with the labels |
| `model_id` | `VARCHAR` | The model that answered; null with the labels |
| `labeled_at` | `TIMESTAMP WITH TIME ZONE` | The end of the batch that answered; null with the labels |
| `transformation_version` | `VARCHAR` | As in every Silver table |

**The definitions**, the same wording for the evaluation labels and the prompt.
`renovation_state` is what the description says about the home as a whole, decided in this
order; the first that applies wins:

1. `needs_renovation`: it needs works or is sold to be renovated ("para reformar", "a reformar",
   "necesita reforma", "para actualizar"). A habitable home for which updating is offered as an
   option is `original`.
2. `new_build`: new construction, never lived in.
3. `renovated`: the home as a whole was renovated, whenever that was ("reformado", "reforma
   integral", "totalmente reformado", "a estrenar tras reforma"). Partial works ("parcialmente
   reformado", a renewed kitchen or bathrooms) are `original`.
4. `original`: habitable as it is, with no whole renovation stated; "buen estado" and "para entrar
   a vivir" belong here.
5. `unknown`: the description says nothing about the state.

`is_auction` is true when the home is acquired through an auction or the assignment of an auction
award ("subasta", "puja", "cesión de remate"), false otherwise, also when the text says it is not
an auction. The exact prompt is `src/enrichment/prompt.py`.

**The reading.**

- A label is the model's reading of the description under one `label_version`, not a fact about
  the home: a description can be wrong, silent or advertising.
- For `tl3` (Claude Haiku 5.5) the evaluation on 230 sale descriptions, labelled by a larger
  language model under the same definitions and weighted by stratum, measured in three runs a
  precision of 0.99 to 1.00 for `renovated`, `needs_renovation` and `is_auction`, and a recall
  of 0.88 to 0.91 for `renovated`, 0.89 to 0.90 for `needs_renovation` and 0.99 to 1.00 for
  `is_auction`. No human labelled the set, so the figures say how closely the small model
  follows the large one; the sample is small, so read them as a few points either way
  (`dev/plans/implementations/FEATURE-012-text-labels.md`).
- A missed `renovated` most often reads as `original`. `unknown` and `original` are the least
  separable values.
- **Null means no evidence:** the text is not labelled yet, the description is blank, or the
  listing is not a sale. It does not mean `unknown`.
- The table is recomputed and replaced whole in every run that processes something and is given
  `--text-labels`, in both modes. A run without the argument leaves an existing table as it is.

## Text labels

The text labeller sends the redacted descriptions of sale listings to a Claude model as a
message batch and stores the answers as an immutable input beside landing, which Silver joins
([ADR 0005](adr/0005-model-derived-labels.md)). Nothing but the redacted description and its own
SHA-256 is sent; no listing id and no text is written to a log.

### The flow

1. A Silver task ends with exit code 0, and a rule starts the labeller.
2. The labeller **collects** every batch it submitted earlier that has ended: it writes the valid
   answers of each as one part. Then it **submits** the pending texts, up to the cap, as one new
   batch, and writes the batch's record. It never waits for a batch.
3. A batch ends within 24 hours, usually within minutes. The next labeller run collects it.
4. The next Silver run that processes a commit writes `listing_text_label` with the new answers.

So a text seen by Silver run N is submitted after N, collected after N+1 and in the table with
N+2. During a week's collection landing commits every three hours, so a new text has its label
within the day. The daily catch-up starts the labeller too, but a Silver run with nothing pending
writes no table: the answers for a week's last commits reach the table with the next landing
commit, on the following Sunday. A manual backfill writes the table at once.

A text is **pending** when it is a non-blank description of a sale listing, has no stored answer
of the current `label_version` and is in no open batch of it. A batch is **open** from its record
until its part exists. The records and the parts are the whole state: there is no table of
batches and no cursor.

### The stored layout

```
<lake-bucket>/enrichment/v1/text_label/label_version=<v>/run=<batch-id>/part-0.parquet
<lake-bucket>/enrichment/v1/text_label_batch/label_version=<v>/batch=<batch-id>/record.parquet
```

- A **part** holds the batch's valid answers, ordered by `text_sha256`: `text_sha256`,
  `label_version`, `model_id`, `renovation_state`, `is_auction`, `labeled_at` (the batch's end),
  `input_tokens`, `output_tokens`. It is written once, also without a row, and never replaced.
- A **record** holds the batch's texts: `text_sha256`, `label_version`, `model_id`, `batch_id`,
  `submitted_at`.
- The records lie beside the answers, never below them, so Silver's read of the answers never
  meets one.

### The labeller command

```bash
python -m src.enrichment.text_labels --catalog <name> \
    --labels <dir-or-s3://lake-bucket/enrichment/v1/text_label> \
    --batches <dir-or-s3://lake-bucket/enrichment/v1/text_label_batch> \
    --model-id <claude-api-model-id> --max-texts <n>
```

It reads the key from the environment variable `ANTHROPIC_API_KEY` and prints counts only:

```
batches collected: 1
batches open: 0
labelled: 1987
invalid answers: 3
errored requests: 10
expired requests: 0
input tokens: 3401000
output tokens: 176000
pending: 812
submitted: 812
remaining: 0
```

- **Invalid answers** (a refusal, an answer cut off or outside the schema) and **errored or
  expired requests** are not stored; their texts are pending again and go into the next batch.
- **The cap** `--max-texts` bounds what one run submits (2,000 in AWS); `remaining` is what the
  next run submits.

| Exit code | Meaning |
| --- | --- |
| 0 | Collected and submitted, or nothing to do. |
| 1 | `ANTHROPIC_API_KEY` is not set; stored answers of the current `label_version` came from another model; a stored part lacks a column; a batch answered for a text outside its record; or, after the run's work is written, a collected batch had no succeeded request: the API credit is used up, the key is revoked or the model is refused. |
| 2 | Usage error. |

### A new label version

`LABEL_VERSION` in `src/enrichment/prompt.py` names the prompt, the schema and the model
together. A change of any of them is a new version, never an edit: every text is pending again
and is labelled anew, at the backlog's cost (below), while the old answers stay in place and
Silver ignores them. An open batch of the old version is never collected. A labeller run refuses
to write when the stored answers of its version came from another model. A new version needs a
new evaluation first.

### The key

- The Claude API key is a SecureString in the Parameter Store, created by the operator by hand;
  Terraform names it (`label_api_key_parameter`) and never reads its value. ECS reads it into the
  labeller's environment; it never enters a file, an argument, the Terraform state or a log.
- Only the labeller's execution role may read the parameter.
- To replace it, create a new key in the Console's workspace, overwrite the parameter
  (`aws ssm put-parameter --name <parameter> --type SecureString --overwrite --value ...`, read
  from a prompt, never typed into a command line that the history keeps), and revoke the old
  key. The next task reads the new one; nothing is applied.

### The API credit

The Claude API is paid from the monthly API credit of the Claude plan, linked to the Console
organization that holds the key. When the month's credit is used up, requests fail, the
collecting run exits 1 and the failure mail follows; the texts stay pending until the credit is
renewed. Nothing is charged, and nothing rolls over. Buying credit or enabling auto-reload in
that organization would lift this cap.

### What the provider keeps

The redacted descriptions leave the AWS account and may be processed outside the EU. A batch's
requests and answers stay readable through the API for 29 days after its creation
([batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing));
everything sent is kept under the retention of Anthropic's
[commercial terms](https://www.anthropic.com/legal/commercial-terms). A batch whose results can
no longer be read is closed with a part without a row, and its texts are submitted again.

### Turning the labeller off

Set `labeller_enabled = false` and apply: no Silver task starts it any more. Silver goes on
writing `listing_text_label` from the answers already stored. Open batches end at the provider
and are collected once the labeller runs again, within 29 days.

## Reference data

Public data that Silver reads beside landing lives in the lake as immutable vintages
([ADR 0004](adr/0004-reference-data.md)):

```
reference/v1/census_sections/vintage=<date>/part-0.parquet
reference/v1/census_sections/vintage=<date>/_manifest.json
```

The census sections are published once a year by the national statistics office (INE) as one
shapefile of the whole country. Nothing in this repository downloads it, and the file never
enters the repository. Once a year, when a new edition is out:

1. **Read the licence again.** ADR 0004 quotes the legal notice as it was read for the first
    vintage. If its terms changed, stop and record the new ones first.
2. **Download** the edition's zip archive from the statistics office's page of the digitised
    census sections. Note the address and the edition's reference date (1 January of its year).
    The projection is named in the archive's `.prj` file; the editions so far use ETRS89 / UTM
    zone 30N, `EPSG:25830`.
3. **Convert** one province into a vintage directory. The command needs DuckDB's spatial
    extension (see the README's Development section) and no network:

    ```bash
    python -m src.reference.census_sections \
        --archive <downloaded.zip> \
        --province 46 \
        --crs EPSG:25830 \
        --vintage <edition date, like 2026-01-01> \
        --source <the address the archive was downloaded from> \
        --licence "CC BY 4.0. Elaboración propia con datos extraídos del sitio web del INE: www.ine.es" \
        --retrieved <the day of the download> \
        --out <local directory>
    ```

    It prints the number of sections written (1,841 for province 46 in the 2026 edition) and
    refuses, with exit code 2 and the cause: an existing vintage directory, an archive without
    exactly one shapefile, a shapefile that lacks a field, a province without a section, and a
    section code that occurs twice. Check `_manifest.json`: it holds the source, the licence, the
    SHA-256 of the archive and the row count.
4. **Upload** the two files. A vintage is written once; `--if-none-match '*'` makes the storage
    refuse an overwrite:

    ```bash
    for file in part-0.parquet _manifest.json; do
        aws s3api put-object --if-none-match '*' \
            --bucket <lake-bucket> \
            --key "reference/v1/census_sections/vintage=<date>/$file" \
            --body "<local directory>/census_sections/vintage=<date>/$file"
    done
    ```

5. **Point the task at it:** set `census_sections` to
    `s3://<lake-bucket>/reference/v1/census_sections/vintage=<date>` in the git-ignored tfvars,
    plan and apply. Only the task definition changes. The next run that processes something
    replaces `listing_location` whole with the new vintage on every row; the old vintage stays
    in the lake.

**Attribution.** Every published result that rests on these boundaries carries the line the
licence asks for: *Elaboración propia con datos extraídos del sitio web del INE: www.ine.es*, with
the edition's date.

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
    is set to its SHA tag and applied; `latest` is refused by validation. DuckDB's extensions
    (`httpfs`, `aws`, `spatial`) are installed when the image is built, so a run downloads none.
- **Reference data.** The task's role may read `reference/v1/` of the lake bucket and cannot
    write it; the operator uploads a vintage. Which vintage a run reads is the variable
    `census_sections`.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `lake_bucket` | — | The private bucket holding landing, reference data and Silver. |
| `silver_image` | — | The image by commit SHA or digest. |
| `census_sections` | `""` | The census-section vintage the task reads, `s3://<lake-bucket>/reference/v1/census_sections/vintage=<date>`; empty passes no `--census-sections`, and the task writes no `listing_location`. |
| `label_model_id` | — | The Claude API model of the current `label_version`, `claude-haiku-5-5` for `tl3`. |
| `label_api_key_parameter` | — | The name of the Parameter Store SecureString that holds the Claude API key. |
| `label_max_texts` | `2000` | Most descriptions one labeller run submits. |
| `labeller_enabled` | `false` | Whether a succeeded Silver task starts the labeller. Turn it on after the first batch of one text is checked. |
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
    --overrides '{"containerOverrides":[{"name":"silver","command":["--landing","s3://<lake-bucket>/landing/v3","--catalog","silver","--mode","backfill","--metrics","--census-sections","s3://<lake-bucket>/reference/v1/census_sections/vintage=<date>","--text-labels","s3://<lake-bucket>/enrichment/v1/text_label"]}]}'

# when the task stopped with exit code 0:
aws lambda update-event-source-mapping --uuid "$UUID" --enabled
```

The override replaces the task definition's whole command, so it names every argument the
scheduled runs get. Leave `--census-sections` and its vintage out only while `census_sections`
is not set: a backfill without them rewrites every other table and leaves `listing_location` as it
was. Keep `--text-labels`: a backfill without it leaves `listing_text_label` as it was.

Messages queued meanwhile stay in the queue and run once the mapping is enabled again.

**A new rule or redaction version.** Deploy the image that carries it first, then run the
backfill above. Incremental starts in between are refused with exit code 3, write nothing and mail
once each; disabling the mapping as above avoids them. Finish a started backfill under the same
image: one that stopped before its ingest log leaves the old versions in the log, so incremental
runs stay refused until a backfill completes.

### The equality check

After a backfill, sync the same landing and compare every table's row count, and the ingest log
against the commits, with a local backfill into a SQLite catalog. Sync right after the task
finishes, so both see the same commits. Compare counts only; never print rows of real data.

### Alarms and failure mail

| Signal | Fires when | First step |
| --- | --- | --- |
| Task-failed rule | A Silver or labeller task stopped with a non-zero exit code or without its container exiting. | Read `/ecs/vlc-housing-silver`, stream prefix `silver/` or `labeller/`. Silver's exit 3 is a refusal: its line names the table and run, the days, or the versions; it is not retried, so every start that meets the same defect mails once. Silver's exit 1 is a failure with a traceback; the queue retries it. The labeller's codes are explained [above](#the-labeller-command). A failed labeller is not retried: the next succeeded Silver task starts it again. |
| `vlc-housing-silver-dead-letter` | A message reached the dead-letter queue: three starts in a row failed (exit 1, or a task that never ran). A refused run never reaches it. | See below. |
| `vlc-housing-silver-pending-runs` | `PendingRuns` stayed above 0 for six hours. | Check the consumer log `/aws/lambda/vlc-housing-silver-consumer` and the last task log. |
| `vlc-housing-silver-unknown-feature-patterns` | A run found a feature wording that is not in the registry. | Read `feature_pattern` for rows with a null `target` and add them to `src/silver/feature_patterns.py` (see [`feature_pattern`](#feature_pattern)). The alarm clears with the first run after the image with the new registry is deployed; runs with nothing pending leave its state unchanged. |

### The dead-letter queue

The dead-letter queue is a circuit breaker, not a store of work: its messages carry nothing,
because the pending runs are still pending in landing. **Never redrive it.**

1. Read the last task's log and the consumer's log.
2. Fix the cause (an image that cannot start, a read that keeps failing, a run longer than the
    consumer can wait for). A contract breach or a version that needs a backfill is a refusal
    (exit 3) and never lands here.
3. Purge the dead-letter queue:
    `aws sqs purge-queue --queue-url "$(aws sqs get-queue-url --queue-name vlc-housing-silver-dead-letter.fifo --query QueueUrl --output text)"`.
4. Queue one run, or wait for the next commit or the catch-up; it processes everything pending.

### Turning it off

Set `trigger_enabled = false` and `catch_up_enabled = false` and apply. Queued messages stay up
to four days; disable `consumer_enabled` as well to stop the consumer from reading them.

### Cost

About $0.77/month for the AWS components: $0.37 dominated by Fargate minutes (1 vCPU, 2 GB,
ARM64, a few minutes a day) and the task's public IPv4 address, and about $0.40 for the custom
metric `UnknownFeaturePatterns` and its alarm. Without that metric and alarm the check costs
nothing and is read from the task log instead. A NAT gateway (≈ $35/month) is avoided by
the public IP; the cheaper alternative is running the command on a laptop at $0, without the
automation. The full estimate is in the FEATURE-004 plan.

The text labeller adds about $0.10/month of Fargate (0.25 vCPU, 1 GB, a minute after each Silver
run) and nothing for its parameter. The Claude API is paid from the plan's credit; measured with
`tl3` (Claude Haiku 5.5, about 1,706 input and 90 output tokens per description, batch prices):

| Model | Per 1,000 texts | A month (~3,250 new sale texts) | The backlog (≥ 15,600 texts) |
| --- | --- | --- | --- |
| Claude Haiku 5.5 (`tl3`) | ~$0.11 | ~$0.35 | ~$1.70 |
| Claude Sonnet 5.5 | ~$1.90 | ~$6 | ~$30 |

Output tokens, which include the model's thinking, drive the cost. A new `label_version`
labels the backlog again. The cheaper alternative is no model at all: keyword rules cost nothing
and read less exactly.

## Snapshots

Each commit's snapshot summary records what built it:

| Property | Example |
| --- | --- |
| `transformation_version` | `silver-v1` |
| `redaction_version` | `r2` |
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
