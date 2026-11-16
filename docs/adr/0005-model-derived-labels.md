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

## Alternatives considered

- **A model call inside the Silver run.** Rejected: Silver would no longer be reproducible, and a
    backfill would pay for every text again.
- **The labeller writing the Silver table.** Rejected: two writers to one catalog, to save a lag
    of hours on attributes that rarely change.
- **An agent runtime** (Amazon Bedrock AgentCore). Rejected: it hosts agent loops with tools,
    sessions and memory; this job is one call per text.
- **Batch inference.** Rejected for now: its discount does not outweigh a second flow and a
    minimum job size at a backlog of thousands of texts and a few hundred new ones a month.
