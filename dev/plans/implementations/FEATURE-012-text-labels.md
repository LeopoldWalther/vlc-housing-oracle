# FEATURE-012 — Labels read from the description: implementation note

**Plan:** [FEATURE-012](../FEATURE-012-text-labels.md) · **Review:**
[REVIEW-FEATURE-012](../../reviews/REVIEW-FEATURE-012.md) · **Recorded:** 2026-11-16

Counts only; no text, no hash and no identifier of real data appears in this note.

## Evaluation set (task 1.2)

Drawn on 2026-11-16 from Silver's sale listings joined to their text row, with the seed
`feature-012-2026-11-16`. Each listing went to the first stratum that applies; a description that
several listings share is one text and belongs to its first stratum. The set, the mapping from
number to stratum and the labels are kept in a private directory outside the repository.

| Stratum | `condition` and keyword | Listings | Distinct texts | Sampled |
|---|---|---|---|---|
| A | auction keyword | 153 | 129 | 40 |
| B | `good`, needs-renovation keyword | 335 | 329 | 30 |
| C | `needs_renovation`, needs-renovation keyword | 880 | 851 | 15 |
| D | `good`, renovated keyword | 3,761 | 3,634 | 40 |
| E | `needs_renovation`, renovated keyword | 180 | 174 | 10 |
| F | `good`, no keyword | 9,599 | 9,139 | 35 |
| G | `needs_renovation`, no keyword | 975 | 944 | 15 |
| H | `new` | 1,225 | 439 | 15 |
| **Total** | | **17,108** | **15,639** | **200** |

Not sampled: 86 listings with an empty description and 42 without a condition. The weight of a
stratum in the evaluation is its number of distinct texts.

### Who labelled

The plan had the user label the texts by hand, and the workbook was mailed on 2026-11-16. On the
same day the user handed the labelling to the assistant. **No human labelled this set.** The
labels are the reading of a larger language model than the one under evaluation, under the label
definitions of the feature plan:

- every text was read in full, in four parts of 50, without its stratum, condition or
  identifier;
- 45 texts were marked as borderline between two labels;
- nine of them, where the parts had read the same pattern differently, were adjudicated to one
  rule, and two labels changed: `needs_renovation` applies only when the text states a need or
  sells the home to be renovated; a habitable home for which updating is offered as an option is
  `original`;
- agency boilerplate about auctions of other properties is not an auction.

What this means for the gate of task 1.6: the evaluation measures the cheap model's agreement
with a stronger model's careful reading, not with a human. Agreement may be somewhat
higher than against human labels, because both readers are language models of one family. The
borderline flags are kept beside the set, so the result can also be read without those 45 texts.
The mailed workbook remains with the user; a human check of a part of the set stays possible and
would replace these labels for the texts it covers.

### Labels

| `renovation_state` | Texts |
|---|---|
| `needs_renovation` | 48 |
| `new_build` | 16 |
| `renovated` | 35 |
| `original` | 32 |
| `unknown` | 69 |

`is_auction` is true for 37 texts: 36 of the 40 of stratum A and one of stratum B, which states
the auction without the keyword.

| Stratum | Sampled | `needs_renovation` | `new_build` | `renovated` | `original` | `unknown` | Auction | Borderline |
|---|---|---|---|---|---|---|---|---|
| A | 40 | 3 | 0 | 1 | 1 | 35 | 36 | 3 |
| B | 30 | 18 | 0 | 2 | 9 | 1 | 1 | 13 |
| C | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 0 |
| D | 40 | 1 | 0 | 29 | 10 | 0 | 0 | 9 |
| E | 10 | 7 | 0 | 2 | 1 | 0 | 0 | 3 |
| F | 35 | 0 | 2 | 1 | 8 | 24 | 0 | 10 |
| G | 15 | 4 | 0 | 0 | 3 | 8 | 0 | 5 |
| H | 15 | 0 | 14 | 0 | 0 | 1 | 0 | 2 |

Two observations for the later tasks:

- The portal's `condition` and the description disagree often. In stratum B, where the portal
  says `good` and the text has a needs-renovation keyword, 18 of 30 texts state a need for
  renovation. In stratum G, where the portal says `needs_renovation` and the text has no keyword,
  8 of 15 texts say nothing about the state.
- Stratum B holds 13 of the 45 borderline texts: "ready to move in, or to update" is where the
  definitions are hardest to apply, for a person and for a model.
