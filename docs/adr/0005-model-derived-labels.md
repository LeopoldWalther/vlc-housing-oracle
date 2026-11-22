# ADR 0005 — Model-derived labels as immutable inputs

**Status:** Accepted · **Date:** 2026-11-16 · **Amends:** [ADR 0003](0003-immutable-bronze-and-landing-per-run.md)

## Context

Some facts about a listing are written only in its free text: whether the home needs renovation,
was renovated or is new, and whether it is sold through an auction. A language model reads them
well enough at a low price (FEATURE-012), but its answer is not reproducible: the same request can
give another answer tomorrow, and a model is retired after a year or two.

Silver must be reproducible. A backfill recomputes every table from landing with pure DuckDB
rules, and two runs over the same commits give the same rows (ADR 0003). A model call inside that
run would break both promises, and a backfill would pay for every text again.

## Decision

**A model's answers are immutable inputs beside landing, not part of the Silver run.**

- This repository's own command labels the redacted descriptions that have no answer yet, one
    model call per text, and writes the answers as Parquet parts under `enrichment/v1/text_label/`.
    A part is written once and never replaced.
- An answer is keyed by `text_sha256`, the SHA-256 of the redacted description, and by
    `label_version`, which names the prompt, the answer schema and the model. The same text in two
    listings is labelled once.
- Silver reads the stored answers like landing: a run given the location joins them to
    `listing_text` by hash under the current `label_version`. The join is a pure rule, so a backfill
    gives the same table again and costs nothing.
- Only the redacted description leaves Silver. Title, location, features and listing identifiers
    are never sent, and no command prints or logs a text, a hash or an identifier.

## Consequences

- A new `label_version` labels every text again, at a cost recorded before the run; answers of
    older versions stay and are ignored.
- Silver has a second input beside landing. Its location is configuration, and a run without it
    writes no label table.
- The labels are inferred, kept in their own table with their model and version, and documented
    with the precision measured on a private evaluation set.
- Processing may happen outside the EU: the model is reached through a global inference profile,
    by the user's decision of 2026-11-16.
- **Amended 2026-11-20:** which service answers is not part of this decision. By the user's
    decision the model is reached through the Claude API, so the redacted description leaves
    the AWS account, and the labeller holds one secret, the API key. A stored answer names its
    model, not its provider.
- **Amended 2026-11-20:** the labeller sends its requests as a batch and collects the answers in
    a later run, so an answer is stored some hours after its text was first seen. What is open
    is read from what is stored: a batch is open from the record of its texts until its part
    exists.

## Alternatives considered

- **A model call inside the Silver run.** Rejected: Silver would no longer be reproducible, and a
    backfill would pay for every text again.
- **The labeller writing the Silver table.** Rejected: two writers to one catalog, to save a lag
    of hours on attributes that rarely change.
- **An agent runtime** (Amazon Bedrock AgentCore). Rejected: it hosts agent loops with tools,
    sessions and memory; this job is one call per text.
- **Batch inference.** Rejected on 2026-11-16 as a second flow, chosen on 2026-11-20 by the
    user's decision: nothing waits for an answer, and a batch costs half.
- **Direct requests in the labeller.** Rejected on 2026-11-20: twice the price and a task that
    runs as long as the texts take, to have an answer hours earlier that nobody is waiting for.
