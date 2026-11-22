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
The user declined a human check of the set on 2026-11-16. After the paid evaluation (task 1.6)
the user may be shown the texts where the model and these labels disagree, and only those.

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

## Paid evaluation (task 1.6)

Run on 2026-11-16 with the evaluation command of task 1.5 and prompt `tl1`, once over the 200
texts. Model-invocation logging is off in the account.

### The planned model is not available

Claude Haiku 5.5 was the plan's model. The account's Anthropic use-case form was submitted and
the model's price offer accepted on 2026-11-16 (the user approved both). The model still answers
every request with "not available for this account", through the global and the EU inference
profile, also after ten minutes of retries, while its agreement, authorisation, entitlement and
region all read as available. Claude Haiku 4.5 answers through the global profile.

The run therefore used **Claude Haiku 4.5** (`global.anthropic.claude-haiku-4-5-20251001-v1:0`).
Its price is ten times that of Haiku 5.5:

| Model | Input / output, per million tokens |
|---|---|
| Claude Haiku 5.5, global profile | $0.10 / $0.50 |
| Claude Haiku 4.5, global profile | $1.00 / $5.00 |

### Report

```
texts: 200
invalid answers: 0
input tokens: 334989
output tokens: 10956
seconds per text: 0.71
renovation_state agreement: 0.76
renovated: labelled 35, model 38, both 33, precision 0.87, recall 0.94, weighted precision 0.88, weighted recall 0.96
needs_renovation: labelled 48, model 39, both 39, precision 1.00, recall 0.81, weighted precision 1.00, weighted recall 0.86
is_auction: labelled 37, model 37, both 37, precision 1.00, recall 1.00, weighted precision 1.00, weighted recall 1.00
```

Per text about 1,675 input and 55 output tokens. The run cost about $0.39.

| Estimate | Haiku 4.5 | Haiku 5.5 (same tokens) |
|---|---|---|
| Per text | $0.00195 | $0.00020 |
| A month (about 3,250 new sale texts) | ~$6.30 | ~$0.65 |
| The backlog (about 15,700 distinct sale texts) | ~$31 | ~$3 |

### Gate

| Value | Weighted precision ≥ 0.90 | Weighted recall ≥ 0.80 | Result |
|---|---|---|---|
| `renovated` | 0.88 | 0.96 | fails on precision |
| `needs_renovation` | 1.00 | 0.86 | passes |
| `is_auction` | 1.00 | 1.00 | passes |

Where the model and the evaluation labels differ on `renovation_state` (48 texts):

| Evaluation label | Model | Texts |
|---|---|---|
| `unknown` | `original` | 29 |
| `needs_renovation` | `original` | 9 |
| `original` | `renovated` | 5 |
| `unknown` | `new_build` | 2 |
| `original` | `new_build` | 1 |
| `renovated` | `new_build` | 1 |
| `renovated` | `original` | 1 |

- All five false positives of `renovated` are homes where only some rooms were renewed (a
  kitchen, bathrooms, a living room, or a partial renovation with a year). The definitions call
  them `original`; the model reads them as renovated as a whole.
- The nine `needs_renovation` texts the model calls `original` are of the kind "habitable, and
  to update" where the text also states a need.
- `unknown` against `original` does not touch a decisive value.

The decision is the user's: which model, and whether `renovated` is dropped, or kept with a
sharper prompt under a new label version and a new run.

## Second paid evaluation: prompt `tl2` (2026-11-19)

The user chose the sharper prompt and a new run. `tl2` changes two things in the prompt and
nothing in the definitions: `renovated` needs a renovation of the home itself, and a home of
which only parts were renewed (a kitchen, bathrooms, a living room, a renovation the text calls
partial) is `original`. The prompt was changed after the kind of the five errors of `tl1` was
known, and it is measured on the same 200 texts, so its result is, if anything, optimistic.

Same model (Claude Haiku 4.5, global profile), same command, one run:

```
texts: 200
invalid answers: 0
input tokens: 355189
output tokens: 10945
seconds per text: 0.80
renovation_state agreement: 0.77
renovated: labelled 35, model 35, both 32, precision 0.91, recall 0.91, weighted precision 0.86, weighted recall 0.93
needs_renovation: labelled 48, model 37, both 37, precision 1.00, recall 0.77, weighted precision 1.00, weighted recall 0.85
is_auction: labelled 37, model 37, both 37, precision 1.00, recall 1.00, weighted precision 1.00, weighted recall 1.00
```

The run cost about $0.41, and the repeated requests below about $0.05.

### Gate

| Value | Weighted precision ≥ 0.90 | Weighted recall ≥ 0.80 | `tl1` | Result |
|---|---|---|---|---|
| `renovated` | 0.86 | 0.93 | 0.88 / 0.96 | fails on precision |
| `needs_renovation` | 1.00 | 0.85 | 1.00 / 0.86 | passes |
| `is_auction` | 1.00 | 1.00 | 1.00 / 1.00 | passes |

`renovated` still fails, although the unweighted precision rose from 0.87 to 0.91.

### What changed between the two prompts

Eleven texts got another `renovation_state`:

| `tl1` | `tl2` | Texts |
|---|---|---|
| `renovated` | `original` | 5 |
| `original` | `unknown` | 2 |
| `needs_renovation` | `original` | 2 |
| `new_build` | `renovated` | 2 |

- Three of the five false positives of `tl1` are gone, two remain (a renewed kitchen; a kitchen
  and bathrooms), and one is new: a home the text calls nearly new, which `tl1` read as
  `new_build` and the evaluation label reads as `original`.
- Two texts labelled `renovated` are now read as `original`: `tl2` is stricter than the labels
  on a home called renovated without detail and on a home of which one floor is renovated.

Where the model and the evaluation labels differ under `tl2` (46 texts):

| Evaluation label | Model | Texts |
|---|---|---|
| `unknown` | `original` | 27 |
| `needs_renovation` | `original` | 11 |
| `renovated` | `original` | 3 |
| `original` | `renovated` | 3 |
| `unknown` | `new_build` | 2 |

### Why the gate's value moved the wrong way: two findings about the measurement

1. **One text decides.** The three false positives lie in stratum D (two) and stratum F (one).
   A sampled text of stratum F stands for 261 distinct texts, one of stratum D for 91. Without
   the single text of stratum F the weighted precision of `renovated` would be 0.94; with it,
   0.86. The sample of 35 texts in the largest stratum is too small to place this value on
   either side of 0.90.
2. **The model's answers are not stable.** The labeller sets no sampling parameter, so the
   model answers with its default temperature. Five texts at the border of `renovated` were
   sent five more times each with `tl2`: three got the same answer every time, two changed
   between runs (three against two, and four against one). One of the two is the text of
   stratum F; one of the three stable texts was answered differently in the evaluation run
   itself. A single run therefore measures `renovated` with a spread of several points, and
   0.88 for `tl1` and 0.86 for `tl2` cannot be told apart.

### Reading

- `is_auction` and `needs_renovation` pass under both prompts.
- For `renovated` neither prompt is shown to pass or to fail. A third prompt would be measured
  with the same spread; the measurement has to become stable first: a fixed temperature where
  the model accepts one, or the majority of several answers, and more texts of stratum F if the
  value is still near the threshold.
- The model is open as well (the user's decision of 2026-11-19: wait). Another model is a new
  `LABEL_VERSION` and a new run in any case, so the decision on `renovated` waits for it.

## Task 1.6 — the gate through the Claude API, prompt `tl3` (2026-11-21)

The amendment of 2026-11-20 moved the labeller to the Claude API (tasks 1.7 to 1.9) and the
prompt to `tl3`, which names no tool. The key was created by the user in a dedicated Console
workspace with a spend limit and reached each command only through its environment. The user
set a budget of $100 a month for testing and up to $5 per run, and asked for Claude Sonnet 5.5
first.

### Stratum F extended

30 more distinct descriptions of stratum F, drawn at random with a recorded seed from the texts
not yet in the set and labelled under the definitions before any model read them, were added.
They keep the stratum's population recorded on 2026-11-16, so all strata keep one reference.

| | Before | After |
|---|---|---|
| Texts | 200 | 230 |
| Stratum F | 35 | 65 |
| Labelled `renovated` | 35 | 38 |
| Labelled `needs_renovation` | 48 | 48 |
| Labelled `is_auction` | 37 | 38 |

A sampled text of stratum F now stands for 141 distinct texts instead of 261.

### One batch of one text (review R3)

One text of the set was sent as a batch of one request with `ClaudeApiBatches` and Claude
Sonnet 5.5. The batch ended after 103 seconds; the request succeeded, its `custom_id` was the
text's hash, and the answer was valid and equal to the set's label (2,373 input tokens, 22
output tokens). The structured output, the `custom_id` and the organization's limits were
accepted, so phase 2 can be built as planned.

### Three runs per model

All 230 answers were valid in every run. Weighted precision / weighted recall:

| Value | Sonnet 5.5, run 1 | run 2 | run 3 | Haiku 5.5, run 1 | run 2 | run 3 |
|---|---|---|---|---|---|---|
| `renovated` | 1.00 / 0.97 | 1.00 / 0.94 | 1.00 / 0.97 | 1.00 / 0.88 | 1.00 / 0.91 | 1.00 / 0.91 |
| `needs_renovation` | 1.00 / 0.89 | 1.00 / 0.89 | 1.00 / 0.89 | 0.99 / 0.90 | 0.99 / 0.89 | 0.99 / 0.90 |
| `is_auction` | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.99 | 1.00 / 0.99 |
| `renovation_state` agreement | 0.90 | 0.90 | 0.90 | 0.89 | 0.88 | 0.88 |

Gate: every decisive value passes with both models in every run (precision ≥ 0.90, recall
≥ 0.80). The spread between runs that left `tl2` undecided is gone: precision is 1.00 or 0.99
throughout, and the extended stratum F no longer lets one text decide.

### Tokens and cost

| | Sonnet 5.5 | Haiku 5.5 |
|---|---|---|
| Input tokens per text | 1,706 | 1,706 |
| Output tokens per text | 31 to 36 | 88 to 91 |
| Cost of one run (direct requests) | about $0.86 | about $0.05 |
| Per 1,000 texts in a batch (half price) | about $1.90 | about $0.11 |
| Backlog, at least 15,639 sale descriptions | about $30 | about $1.70 |

The runs of the day cost about $2.75 in all. A month of new texts costs about $0.11 per 1,000
new descriptions with Haiku 5.5. The backlog counts the sale descriptions in the eight
strata; texts outside them add to it in proportion.

### Decision

The user chose **Claude Haiku 5.5** (2026-11-21): it passes the gate at a twentieth of the
price of Sonnet 5.5, whose only advantage is a higher recall of `renovated`. `tl3` is the
prompt of `src/enrichment/prompt.py` with `claude-haiku-5-5`; a change of the prompt or the
model from here on is a new `LABEL_VERSION` and a new evaluation.
