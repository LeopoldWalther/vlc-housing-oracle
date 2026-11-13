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

## Completeness

Source: [`01_completeness.sql`](../../analysis/silver-quality/01_completeness.sql). Rates are
shares of the listings with a detail record.

- **Listings without a detail record:** 11 of 19,049 listing rows were only seen in search
  results; they carry no operation and no attributes. Every other listing has a detail record.
- **Empty search cards:** 0.06% of sale observations, none for rent.

| Attribute | sale | rent | Reading |
| --- | --- | --- | --- |
| `price_eur`, `sqm_built` | 100% | 100% | Always present |
| `bathrooms`, `condition`, `property_type` | 99.4% / 99.7% / 99.8% | 100% / 100% / 99.7% | Practically always present |
| `latitude`, `longitude` | 99.2% | 99.3% | 118 sale and 35 rent listings have no coordinates |
| `rooms` | 91.9% | 81.9% | See below: one-room homes are missing |
| `page_district` | 83.6% | 92.9% | |
| `is_exterior` | 75.6% | 88.0% | |
| `year_built` | 71.1% | 45.1% | |
| `floor` | 65.4% | 78.3% | Houses have none; flats do not always state it |
| `neighborhood` | 52.6% | 76.6% | Too sparse for a hard rule |
| `sqm_usable` | 48.3% | 45.6% | |
| `energy_consumption_rating` / `energy_emissions_rating` | 90.6% / 59.7% | 92.6% / 70.0% | Includes "in progress" and "exempt" states |
| `has_heating` / `heating_type` | 58.3% / 17.7% | 68.3% / 26.9% | |
| `sqm_plot`, `storeys` | 15.7% / 13.4% | 5.8% / 6.4% | House attributes |
| `prev_price_eur` | 10.6% | 7.6% | Present only when the page shows a price drop |
| `community_fee_eur_month` | 2.1% | 0% | Unusable |

The feature flags with a state column (`has_elevator_state`, `has_terrace_state` and the others)
are always filled, because "not mentioned" is a state of its own.

**`rooms` is never 1.** The smallest value of `rooms` is 2 in both operations
([`02_plausibility.sql`](../../analysis/silver-quality/02_plausibility.sql): the 0.1st percentile
is 2), and a private aggregate count on the same snapshot confirmed that no listing has
`rooms = 1`. `rooms` is missing for 1,170 sale and 839 rent listings, of which only 184 and 251
are studios. The remaining 986 sale and 588 rent listings (6.8% and 12.7%) are most likely
one-room homes whose singular room label is not read upstream. This is a defect to report to the
acquisition side, not a property of the market. Until it is fixed, a missing `rooms` on a
non-studio means "probably 1", and no duplicate tier can match these listings, because a missing
value never equals another.

## Plausibility

Source: [`02_plausibility.sql`](../../analysis/silver-quality/02_plausibility.sql), thresholds in
its `params` block.

| Value | Operation | p1 | p50 | p99 | p99.9 |
| --- | --- | --- | --- | --- | --- |
| Price (EUR) | sale | 81,361 | 296,231 | 1,844,990 | 4,559,170 |
| Price (EUR per month) | rent | 799 | 1,512 | 5,070 | 13,500 |
| Built area (m²) | sale | 41 | 107 | 689 | 1,454 |
| Built area (m²) | rent | 30 | 92 | 379 | 1,003 |
| Price per built m² (EUR) | sale | 622 | 2,804 | 7,705 | 11,607 |
| Price per built m² (EUR per month) | rent | 7.2 | 16.9 | 41.6 | 55.6 |
| Year built | sale | 1897 | 1974 | 2026 | 2027 |
| Year built | rent | 1884 | 1980 | 2026 | 2026 |

The central ranges are plausible for both operations. The rule candidates hit tens of listings,
not hundreds, so flags will not dominate any analysis:

| Rule candidate | Threshold | sale | rent | Decision |
| --- | --- | --- | --- | --- |
| Price outside bounds | sale 10,000 to 20,000,000; rent 100 to 30,000 | 0 | 0 | Adopt as a guard; nothing to flag today |
| Built area below minimum | 10 m² | 0 | 0 | Adopt as a guard |
| Built area above maximum | 2,000 m² | 5 | 0 | Adopt |
| Price per m² below minimum | sale 300; rent 2 | 22 | 3 | Adopt; 17 of the 22 are houses, where plot-heavy listings pull the value down, so the flag means "check", not "wrong" |
| Price per m² above maximum | sale 15,000; rent 60 | 5 | 2 | Adopt |
| Usable area above built area | – | 0 | 0 | Adopt as a guard |
| No rooms outside studios | `rooms = 0` | 0 | 0 | Drop: the value 0 does not occur; the real gap is the missing one-room value above |
| Rooms above maximum | 12 | 10 | 0 | Adopt |
| Bathrooms above rooms plus two | – | 9 | 2 | Adopt; 9 of the 11 are houses |
| Year built out of range | 1500 to 2027 | 4 | 0 | Adopt; raise the upper bound with the calendar year plus one |
| Price drop above maximum | 50% against `prev_price_eur` | 0 | 0 | Adopt as a guard |
| Sale priced like a rent | below 5,000 | 0 | 0 | Drop: covered by the price bounds |
| Rent priced like a sale | above 50,000 | 0 | 0 | Drop: covered by the price bounds |

**Search price against detail price.** Of the listings whose search-result sighting and detail
record fall on the same day, 15 of 14,390 sale and 5 of 4,639 rent listings show different
prices (0.1%). The two sources agree; no rule is needed.

Adopted rules together flag about 55 sale and 7 rent listings before overlaps (0.4% and 0.2%).

## Location

Source: [`03_location.sql`](../../analysis/silver-quality/03_location.sql).

- **Completeness and precision:** 99.2% of listings have coordinates. 89% of them carry seven
  decimal places and under 0.1% carry four or fewer, so the values are not visibly rounded.
- **Placement:** no coordinate lies outside the province box. The distance from the
  municipality's median coordinate has a median of about 1.5 km and a 99.9th percentile of 12 km; 107
  sale and 6 rent listings lie beyond 10 km, none beyond 20 km.
- **Shared points:** 1,313 sale listings sit on 322 exact points used by more than one listing,
  and 172 rent listings on 80; the largest point holds 24 sale listings. Shared points are
  developments and agencies that pin one address, and to some extent portal-side snapping. Equal
  coordinates are therefore weak evidence of one property: 356 sale pairs of tier B share a point,
  but only 50 of tier C do ([`04_duplicates.sql`](../../analysis/silver-quality/04_duplicates.sql)),
  which is the signature of several units at one address.
- **District:** where both are present, `district` and `page_district` differ for 5 of 7,436 sale
  listings and for none of 3,554 rent listings. 3 of 84 neighbourhood names occur in more than one
  district, so a neighbourhood needs its district to be unambiguous.

Proposed rules: flag missing coordinates, and flag a listing farther than 10 km from its
municipality's median coordinate (0.7% of sale, 0.1% of rent). Coordinates are precise enough to
compare listings at building level; they cannot tell units of one building apart.

## Duplicates

Source: [`04_duplicates.sql`](../../analysis/silver-quality/04_duplicates.sql).

| Tier | sale pairs | sale listings in a pair | rent pairs | rent listings in a pair |
| --- | --- | --- | --- | --- |
| A0 (shape key) | 68,418 | 9,280 | 20,225 | 2,859 |
| A±10 | 18,368 | 7,580 | 6,076 | 2,296 |
| A±5 | 11,554 | 6,763 | 3,310 | 1,947 |
| A (equal price) | 4,100 | 3,904 | 1,464 | 1,365 |
| B (A±5 and near) | 4,309 | 3,890 | 574 | 729 |
| C (B and equal floor) | 2,513 | 2,278 | 348 | 447 |

- **The shape key alone is far too wide.** 64% of all sale listings share it with another
  listing, and the median price gap inside an A0 pair is 23% (sale) and 21% (rent): most A0 pairs
  are merely similar flats in one municipality.
- **Tier A as clusters:** 1,520 sale clusters and 520 rent clusters; 69% and 66% of them have two
  listings, 93 and 39 have five or more. Counted naively, tier A overstates supply by 2,384 sale
  and 845 rent listings (17% and 18%). The manual check shows that this exact-price key is not a
  duplicate rule either: it ignores location.
- **Concurrent, with different texts.** 93% of tier A sale pairs and 84% of rent pairs are online
  at the same time, and only 6% and 2% share an identical redacted description. That is one flat
  advertised by several agencies, each with its own text. Text identity is a weak signal in both
  directions: developments reuse one text across units.
- **Re-listings exist but are the minority.** After the ramp-up, 196 sale and 42 rent pairs of
  tier C are sequential (8% and 12%).
- **Tier C,** the closest tier to the certain rule: 15.8% of sale and 9.6% of rent listings are in
  at least one pair. The surplus, listings minus distinct properties, lies between 1,139 and 2,277
  for sale (7.9% to 15.8% of all sale listings) and between 224 and 348 for rent (4.8% to 7.5%).

### Expected flag rate of the certain rule

The certain rule of the manual check is tier C, widened by resale pairs with a missing floor and
narrowed by the new-build condition, with a round 350 m in place of the tier's coordinate box. It
therefore flags more listings than tier C and fewer than tier B: between 15.8% and 27.0% of sale
listings and between 9.6% and 15.7% of rent listings are in at least one pair, and the true
surplus is at least 8% of sale and 5% of rent listings. The follow-up feature measures the exact
rate when it implements the rule; no committed query computes it yet.

What the rule cannot see: pairs across sources (one source today), pairs whose built area or
room count differs by one unit between the two advertisers, and every listing with a missing
`rooms`.

## Temporal

Source: [`05_temporal.sql`](../../analysis/silver-quality/05_temporal.sql). `RAMP_UP_END` is
2026-09-30; the last collection day is 2026-10-30, so `CENSOR_START` is 2026-10-23.

- **Sweep rhythm.** An area is walked completely again after a median of 7.4 days (25th to 90th
  percentile: 5.8 to 13), and a listing is seen again after a median of 7 days. The seven-day
  windows of the plan hold.
- **Missed listings are rare.** Of 30,975 sightings expected on days when a listing's area was
  walked completely between its first and last sighting, 250 are missing (0.8%), spread over 196
  listings. Collection does not lose listings inside a complete walk in any way that matters.
- **New listings.** 3,466 sale and 1,351 rent listings were first seen after the ramp-up. Sale
  listings arrive at 520 to 820 per week. Rent listings appear only in every second week (324,
  418 and 609), which reflects when rent areas were walked, not when flats were published.
- **Listings that disappeared.** Of the listings first seen after the ramp-up, 453 sale (13.1%)
  and 358 rent (26.5%) were last seen before `CENSOR_START` and were absent from a later complete
  walk of their area. Half of the ended rent listings were seen on one day only, and the median
  ended sale listing spans about three days; 90% end within ten days. The manual check agrees: 20 of
  the first 46 pairs opened had a page that was gone within days of the snapshot. Supply turns
  over fast, and a weekly sweep sees a short-lived listing once or not at all, so observed
  lifetimes are censored at both ends and biased short. Disappearance stays an observation, not a
  sale or a rental.
- **Price changes.** Detail records are rarely fetched twice (55 listings), so they show almost
  nothing: 6 changes, all downwards. Search-result sightings are the usable source: 2,134 of
  16,021 listings seen more than once changed their price (13.3%), 1,935 downwards and 190
  upwards.
- **Runs.** The snapshot holds 213 collection runs, one of them the import of the history before
  per-run commits, and no replay. 53 runs after the import delivered no observation, and none of
  them reported a search result.

### The late-October collection stall

The 53 empty runs are explained by the acquisition side, not by Silver:

- By 2026-10-27, all areas of that week but one were finished. The last area was empty on the
  portal, and the collector treated the resulting error as temporary. The area never counted as
  done and was retried without result at every run until 2026-10-30.
- The defect was fixed in the acquisition repository on 2026-10-30. The remaining runs of that
  week were empty by design, because every area was done, and collection resumed with the new
  week on 2026-11-01.
- The budget gate admitted every run in that period, and the logs show no blocking by the portal.
- Five runs on 2026-10-25 failed for a cause that could not be established, because the collector
  did not log the HTTP status on abort.

Two consequences for monitoring: a run without observations is normal once a week's areas are
done, so the health signal is "no run reported a finished week for seven days", not "a run
delivered nothing". That alarm is planned on the acquisition side.

## Redaction

Source: [`06_redaction.sql`](../../analysis/silver-quality/06_redaction.sql), over 19,038
descriptions. Title, location and features have no match for any pattern.

| Pattern | Descriptions | Reading |
| --- | --- | --- |
| `r1`'s own phone pattern | 0 | As required |
| Phone digits glued to a `34` country code | 1 | A real contact detail that `r1` missed |
| Phone digits glued to a letter | 0 | |
| Phone digits inside a longer number | 11 | Most likely reference numbers, not phones |
| Honorific followed by a lower-case or all-caps word | 3 | One name in capitals, two ordinary words |
| E-mail address | 0 | |
| Web address / web address split by a space | 2 / 21 | Agency sites; not personal data |
| Holding `[phone]` / `[name]` placeholders | 587 / 49 | What `r1` redacted |

The two confirmed defects were reported at once and fixed as redaction `r2` in FEATURE-006. After
its backfill of 2026-11-05 the glued country code matches 0 descriptions and only the two
ordinary words remain; the counts are in the
[FEATURE-006 implementation note](../../dev/plans/implementations/FEATURE-006-redaction-r2.md).
Snapshots older than that backfill, including the set this document reports on, still hold `r1`
text in the private lake until they are expired.

## What this means for the candidate use cases

| Use case | Main risks found | Minimum requirement on a listing |
| --- | --- | --- |
| Fair-price estimation | Duplicates weight one flat several times (at least 8% of sale listings are surplus); `year_built` is missing for 29% of sale and 55% of rent listings, `sqm_usable` for about half | Price, built area, coordinates, `property_type`, `condition`; one representative per property group; no plausibility flag |
| Fix-and-flip arbitrage | Depends on `condition`, which is complete, and on the price per m², whose low tail is dominated by houses with plots | As fair price, plus `condition`; treat the low price-per-m² flag as "check" |
| Buy-to-let yield maps | Rent supply is a third of the sale supply and `neighborhood` is missing for 47% of sale listings; `community_fee_eur_month` is unusable | Coordinates rather than neighbourhood names; duplicates collapsed in both operations |
| Liquidity and time on market | Lifetimes are censored by the weekly sweep, a re-listing under a new id resets the clock, and 13% to 27% of new listings end within the window | Property groups across time (sequential duplicates), first-sweep listings excluded, and more than one month of history |
| Market dynamics | New-listing counts follow the walk schedule; price changes are only visible in search-result sightings | Weekly, not daily, series; areas compared only over complete walks |

The one-room gap affects every use case that segments by rooms and should be fixed upstream
before a model is trained.

## Proposed rules

All rules flag and never filter: no Silver row is changed or dropped, and a consumer decides which
flags exclude a listing from its analysis.

| Rule | Definition | Expected rate on this snapshot |
| --- | --- | --- |
| Certain duplicate | The rule of the manual check, as connected groups | 16% to 27% of sale and 10% to 16% of rent listings in a group |
| Possible duplicate (optional) | The relaxations of sample 3: resale with different floors, price gap of 5% to 10% | Not measured; precision about 50% to 70% |
| New-build unit | `condition = 'new'` and in a pair that fails only on the floor | Not measured |
| Implausible value | The adopted plausibility rules, one flag each | 0.4% of sale, 0.2% of rent |
| Misplaced or missing coordinates | No coordinates, or farther than 10 km from the municipality's median | 1.6% of sale, 0.9% of rent |
| Incomplete for a use case | The minimum requirements above, one flag per use case | Driven by `year_built` and `sqm_usable` where a use case needs them |

## Sketch of the follow-up feature

- **A labels table next to the facts,** in Gold, keyed by listing and carrying a
  `quality_version`: one row per listing with its plausibility and location flags, a
  `property_group_id`, and the group's confidence (`certain`, later perhaps `possible`). Silver
  stays untouched, so a rule change is a new `quality_version`, not a rewrite of history.
- **Groups, not pairs.** The pair rule is not transitive; the feature builds connected components
  and has to bound them, because a chain of near pairs can link flats that are 350 m apart at
  each step and far apart overall.
- **Rules as tested code.** Each adopted rule gets a red test on the synthetic landing data
  first; the synthetic generator needs duplicate groups, a new-build development and implausible
  values as fixtures.
- **Measured on real data once, privately,** with the flag rates of this document as the expected
  values, and the exact rate of the certain rule recorded then.
- **Upstream reports,** outside this repository: the missing one-room value, and the health alarm
  for a week without a finished sweep.

## Limits of these findings

- The data are young: 23 collection days over five and a half weeks. Temporal findings are early and
  must be measured again before a liquidity use case relies on them.
- The manual check judged fewer pairs than planned and only pairs that were still online; it
  supports the certain rule and does not measure recall.
- Everything here describes one source and redaction `r1`.
