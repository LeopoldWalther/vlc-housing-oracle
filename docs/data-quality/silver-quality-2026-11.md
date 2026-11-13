# Silver data quality — findings of November 2026

Outcome of FEATURE-005 ([plan](../../dev/plans/FEATURE-005-silver-quality-exploration.md)). It
reports how trustworthy Silver is and proposes the quality rules a follow-up feature should
implement. Counts, rates and percentiles only: no listing id, page link, coordinate, price of a
single listing or text excerpt appears here.

## Snapshot set

Every number from a query file was measured on the Silver snapshot set of 2026-10-31, which the
files under [`analysis/silver-quality/`](../../analysis/silver-quality/) pin with
`FOR VERSION AS OF`. The six files were rerun unchanged on 2026-11-13 and returned the numbers
below.

| Table | Snapshot id |
| --- | --- |
| `listing` | `2497744844056595693` |
| `listing_detail` | `1248122765974686753` |
| `listing_text` | `4512740065342526121` |
| `observation` | `1037665036251468126` |
| `walk` | `7332850479666614547` |
| `landing_ingest` | `7987848718630309265` |

The set holds 19,038 listings with a detail record (14,399 sale, 4,639 rent), collected on 23
days between 2026-09-23 and 2026-10-30, from a single source, under redaction `r1`. The Silver
backfill of 2026-11-05 (FEATURE-006) replaced the current snapshots; the pinned ones stay readable
until they are expired.

## Manual precision of the duplicate strata

The tiers A0, A±10, A±5, B and C are defined in the
[exploration README](../../analysis/silver-quality/README.md). The operator judged pairs from the
listing pages, privately; the samples, their ids and links never entered the repository. A verdict
is `same` (one property), `different`, `project` (different units of one new-build development),
`unsure`, or `gone` (a page was no longer online).

### What was sampled

The plan asked for 30 judged pairs in each of four exclusive strata. That was not reached, for two
reasons found on the way, and the check became three smaller samples instead:

- listings leave the portal fast: 20 of the first 46 pairs opened were `gone` within days of the
  snapshot, so later samples were drawn only from listings still online;
- the first verdicts showed that the tiers were the wrong cut (new-build units, unreliable floors),
  so the later samples tested the questions that decide the rule, not the original strata.

| Sample | Drawn from | Pairs | Purpose |
| --- | --- | --- | --- |
| 1 | Snapshot set of 2026-10-31, 30 concurrent pairs per stratum, ordered by a hash of the pair ids | 120 | The plan's four strata |
| 2 | Silver as of 2026-11-04, listings seen since 2026-11-01, both pages online, no pair of sample 1 | 56 | Strata 2 to 4 again, judgeable |
| 3 | Silver as of 2026-11-08, both pages online, same rooms and bathrooms, closer than 350 m, no pair of samples 1 and 2 | 28 | Four relaxations of the rule, 7 pairs each |

### Verdicts

Sample 1. Only the first two strata were judged; strata 3 and 4 were replaced by sample 2.

| Stratum | same | different | unsure | gone | not judged |
| --- | --- | --- | --- | --- | --- |
| C | 18 | 0 | 0 | 12 | 0 |
| B without C | 4 | 0 | 4 | 8 | 14 |
| A±5 without B | – | – | – | – | 30 |
| A0 without A±10 | – | – | – | – | 30 |

The four `unsure` pairs of "B without C" carried the operator's note that they were units of one
new-build development; sample 2 therefore got the verdict `project`.

Sample 2.

| Stratum | same | different | project | unsure | gone | not judged |
| --- | --- | --- | --- | --- | --- | --- |
| B without C | 14 | 0 | 2 | 0 | 3 | 0 |
| A±5 without B | 1 | 16 | 0 | 1 | 1 | 0 |
| A0 without A±10 | 0 | 11 | 0 | 0 | 1 | 6 |

Within "B without C", the 14 `same` pairs split by floor: in 11 the floor is missing on at least
one side, in 3 both floors are given and differ. All 14 are resale. Both `project` pairs are new
build with different floors.

Sample 3.

| Stratum | same | different | project | unsure | gone | Precision |
| --- | --- | --- | --- | --- | --- | --- |
| Resale, floors given and different | 2 | 2 | 0 | 0 | 3 | 2 of 4 |
| Built area within 5%, not equal | 1 | 6 | 0 | 0 | 0 | 1 of 7 |
| Price gap between 5% and 10% | 3 | 3 | 0 | 1 | 0 | 3 of 6 |
| New build, floor missing on one side | 0 | 1 | 6 | 0 | 0 | 0 of 7 |

### What the verdicts show

- **Tier C is precise.** 18 of 18 judgeable pairs are the same property.
- **A missing floor does not hurt on resale.** 11 of 11 resale pairs of tier B with a missing
  floor are the same property.
- **No tested relaxation is certain.** A built area that differs by up to 5% is 1 of 7 `same`, a
  price gap of 5% to 10% is 3 of 6, and a different floor on resale is 2 of 4 in sample 3 and 5
  of 7 over samples 2 and 3 together.
- **Units of a new-build development are their own class.** They share operation, municipality,
  area, rooms, bathrooms, price band and location, and often the description, and are still
  different flats. Only the floor separates them: with the floor missing on one side, 0 of 7 pairs
  are the same property and 6 are units of one development.
- **Distance has to stay tight.** "A±5 without B" is 1 of 17 `same`. A draft rule with a 1 km
  radius accepted two `different` pairs of sample 2, both between 450 m and 500 m apart, while
  every `same` pair it accepted lay closer than 350 m.
- **Hard-mismatch hints are unreliable.** Sample 1 was prefilled with an automatic hint from
  Silver. In tier C, 6 of the 18 `same` pairs carried the hint "likely different", raised by a
  different `property_type` or `sqm_usable`. The two listings of one property routinely disagree
  on these fields, so neither may veto a duplicate.
- **The sample sizes are small.** 29 of 29 supports "certain"; it does not measure a precision to
  the percent, and recall is known only roughly: the rule below deliberately misses a good third
  of the `same` pairs of samples 2 and 3 (resale with contradicting floors, price gaps of 5% to
  10%).

### The rule these verdicts support

Two listings are **certainly the same property** when all of these hold:

- same operation, municipality, rooms and bathrooms;
- built area exactly equal;
- price gap at most 5% of the lower price;
- distance below 350 m;
- floor:
    - new build on either side (`condition = 'new'`): present on both sides and equal;
    - resale: equal, or missing on at least one side.

Not criteria: the source, the time online, the neighbourhood or district (missing too often, see
completeness), `property_type` and `sqm_usable`.

Evidence: 18 of 18 (sample 1, tier C) and 11 of 11 (sample 2, resale with a missing floor), with
no counter-example in any sample.
