# Gold: `listing_property_group`

The first Gold dataset. It maps every listing with a detail record to a `property_group_id`, so
that listings which are certainly the same property, such as one flat advertised by several
agencies, count once. Counting distinct `property_group_id` counts properties.

The grouping is an inferred label next to the facts: Silver is not changed, no listing is dropped,
and a group says "certainly the same property under rule `pg1`", never sold, rented or re-listed.

## The rule `pg1`

Two listings with a detail record form a pair when all of these hold:

- equal `operation`, `municipality`, `rooms`, `bathrooms` and `sqm_built`, none of them missing;
- the higher `price_eur` is at most 5% above the lower, compared in integers
    (`100 × higher ≤ 105 × lower`), so exactly 5% pairs;
- both have coordinates and lie less than 350 m apart (haversine distance on a sphere of radius
    6,371 km);
- floor: if either listing's `condition` is `new`, both floors are present and equal; otherwise
    the floors are equal or at least one is missing. A missing `condition` counts as not new.

The source is not compared, so listings of two sources can pair, and so can a listing and its
later re-listing. The thresholds are named constants in `src/gold/property_group.py`; the rule is
`src/gold/sql/pairs.sql`. The evidence for it is the manual precision check of the data-quality
findings ([`docs/data-quality/silver-quality-2026-11.md`](data-quality/silver-quality-2026-11.md)):
29 of 29 judgeable pairs were the same property, and every relaxation tested was uncertain.

A **group** is a connected component of the pairs: the pair rule is not transitive, and a
partition of the listings has to be. A listing in no pair is its own group of one.

A change of the rule is a new `property_group_version`, never an edit of `pg1`.

## Columns

One row per listing with a detail record, ordered by `source`, `source_listing_id`:

| Column | Type | Meaning |
| --- | --- | --- |
| `source` | `VARCHAR` | As in Silver |
| `source_listing_id` | `VARCHAR` | The listing |
| `property_group_id` | `VARCHAR` | `<source>:<source_listing_id>` of the group's smallest member, ordered by `(source, source_listing_id)` |
| `group_size` | `INTEGER` | Listings in the group; 1 for a listing without a duplicate |
| `group_is_complete` | `BOOLEAN` | Every two members satisfy the pair rule; false marks a group held together by a chain |
| `property_group_version` | `VARCHAR` | `pg1` |
| `silver_listing_snapshot_id` | `BIGINT` | The snapshot of Silver's `listing` the build read |
| `built_at` | `TIMESTAMP WITH TIME ZONE` | When the build ran, UTC |

The file is `<out>/listing_property_group/part-0.parquet`; a new build overwrites it.

## Limits

- **Stable within one build only.** When a listing joins a group or two groups merge, the
    `property_group_id` can change with the next build. Join on it only within one build; a
    consumer that needs a stable id pins the `silver_listing_snapshot_id` it was built from.
- **Groups of more than two listings are not manually validated.** The precision check judged
    pairs, never groups. A large group may be a development whose units are not marked as new
    build. `group_size` and `group_is_complete` are on every row, so a consumer can cap the size
    it trusts or exclude incomplete groups; the command reports both.
- **Recall.** The rule is deliberately strict and misses roughly a third of true duplicates (see
    the findings), so supply counted on it is still overstated.
- **One-room homes cannot pair.** `rooms` is missing for most of them upstream, and a missing
    value never matches.

## Privacy

The dataset is listing-level and stays in the private lake, under the versioned Gold prefix
beside `landing/` and `silver/`. It is never published, and neither is its location; only
aggregates derived from it are. The command prints counts only.

## Build

The build runs by hand until a consumer needs it after every Silver run:

```bash
python -m src.gold.property_group --catalog <name> --out <dir-or-s3://lake-bucket/gold/v1>
```

`--catalog` names a PyIceberg catalog configured as for Silver (see
[Catalog entries](silver-rebuild.md#catalog-entries)). `--out` is a directory or an `s3://`
location that already names the versioned Gold prefix; an `s3://` location uses the default AWS
credential chain. The build takes the current snapshot id of `vlc_housing_silver.listing` once
and reads exactly that snapshot, so the recorded id is the one read even if Silver commits
meanwhile.

It prints, as counts: listings, groups (of two or more listings), surplus listings (listings in a
group beyond the first), incomplete groups, groups above five listings, the largest group, and
the Silver snapshot id.

| Exit code | Meaning |
| --- | --- |
| 0 | Built and written. |
| 1 | Refused: the catalog has no `vlc_housing_silver.listing` table, or it has no snapshot. |
| 2 | Usage error, for example a missing `--out`. |

### On synthetic data

With the `local` catalog of the Silver runbook, after a backfill of the synthetic landing:

```bash
python -m src.silver.rebuild --landing /tmp/landing-v3 --catalog local --mode backfill
python -m src.gold.property_group --catalog local --out /tmp/gold/v1
```

The synthetic landing holds one property under two listing ids and a unit of a new build beside
it on another floor, so the build reports one group of two and every other listing alone.
