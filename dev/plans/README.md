# Feature planning workflow

Feature plans for this repository, produced by the **Architect · Review · Implement** workflow
(see [`.claude/WORKFLOW.md`](../../.claude/WORKFLOW.md)). This file is the feature registry, not a
speculative roadmap: add a row only when its top-level plan exists.

## Registered features

| ID | Title | Status | Branch | Effort | Priority | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| FEATURE-001 | [Silver on Iceberg against landing/v1](FEATURE-001-silver-rebuild.md) | 🟢 Complete | `feature/silver-rebuild` | L (~16-20h) | High | architect |
| FEATURE-002 | [Listing events: disappeared and reappeared](FEATURE-002-listing-event.md) | 🟢 Complete | `feature/listing-event` | M (~8h) | High | architect |
| FEATURE-003 | [Incremental Silver from landing v2, run by AWS](FEATURE-003-incremental-silver.md) — superseded by ADR 0003 before implementation | ⚪ Superseded | `feature/incremental-silver` | L (~20-24h) | High | architect |
| FEATURE-004 | [Incremental Silver from landing v3, run by AWS](FEATURE-004-incremental-silver-from-landing-v3.md) | 🟢 Complete | `feature/incremental-silver-v3` | L (~28h) | High | architect |
| FEATURE-005 | [Silver data-quality exploration](FEATURE-005-silver-quality-exploration.md) | 🟢 Complete | `feature/silver-quality-exploration` | M (~10h) | High | architect |
| FEATURE-006 | [Redaction r2](FEATURE-006-redaction-r2.md) | 🟢 Complete | `feature/redaction-r2` | S (~3h) | High | architect |
| FEATURE-007 | [Property groups: one property behind several listings](FEATURE-007-property-groups.md) | 🟢 Complete | `feature/property-groups` | M (~10h) | High | architect |
| FEATURE-008 | [Listing quality flags: implausible values and misplaced coordinates](FEATURE-008-listing-quality.md) | 🟢 Complete | `feature/listing-quality` | M (~7h) | High | architect |
| FEATURE-009 | [Listing price history](FEATURE-009-listing-price-history.md) | 🟢 Complete | `feature/listing-price-history` | S (~6h) | High | architect |
| FEATURE-010 | [Labels from listing features, and a check for unread features](FEATURE-010-feature-labels.md) | 🟢 Complete | `feature/feature-labels` | M (~9h) | High | architect |
| FEATURE-011 | [Listing location from coordinates: municipality, district and census section](FEATURE-011-listing-location.md) | 🟢 Complete | `feature/listing-location` | M (~11h) | Medium | architect |
| FEATURE-012 | [Labels read from the description by a language model](FEATURE-012-text-labels.md) | 🟡 In progress | `feature/text-labels` | L (~26h) | Medium | architect |
| FEATURE-013 | [Property groups move from Gold to Silver](FEATURE-013-property-groups-in-silver.md) — implemented first, before FEATURE-009 to FEATURE-012 | 🟢 Complete | `feature/property-groups-in-silver` | M (~6h) | High | architect |
| FEATURE-015 | [Run guards: a Silver run refuses what it cannot do correctly](FEATURE-015-run-guards.md) | 🔵 Planned | `feature/run-guards` | M (~9h) | High | architect |
| FEATURE-016 | [Alarms for silence, a run-time limit and run telemetry](FEATURE-016-silence-alarms.md) | 🔵 Planned | `feature/silence-alarms` | M (~10h) | High | architect |
| FEATURE-017 | [Labeller run safety: a served version, one run at a time, stable answers](FEATURE-017-labeller-run-safety.md) | 🔵 Planned | `feature/labeller-run-safety` | M (~10h) | High | architect |
| FEATURE-018 | [Feature wordings by digest: the tree no longer quotes the source](FEATURE-018-feature-wording-digests.md) | 🔵 Planned | `feature/feature-wording-digests` | S (~6h) | High | architect |
| FEATURE-019 | [Pinned deployment: one commit, one digest, one reviewed plan](FEATURE-019-pinned-deployment.md) | 🔵 Planned | `feature/pinned-deployment` | M (~9h) | Medium | architect |
| FEATURE-020 | [Run manifest, rollback and snapshot expiry](FEATURE-020-run-manifest.md) | 🔵 Planned | `feature/run-manifest` | M (~14h) | High | architect |
| FEATURE-021 | [Silver follows landing v4, and the contract has one source](FEATURE-021-landing-v4.md) | 🔵 Planned | `feature/landing-v4` | M (~12h) | Medium | architect |
| FEATURE-022 | [One declaration per Silver table, and `rebuild.py` cut into modules](FEATURE-022-table-registry.md) | 🔵 Planned | `feature/table-registry` | L (~18h) | Medium | architect |
| FEATURE-023 | [A way past a refused commit, and a writer lease](FEATURE-023-refused-commit-quarantine.md) | 🔵 Planned | `feature/refused-commit-quarantine` | L (~16h) | Medium | architect |
| FEATURE-024 | [Redaction r3, a residue count per run and the privacy record](FEATURE-024-redaction-r3.md) | 🔵 Planned | `feature/redaction-r3` | L (~14h) | Medium | architect |

**Status legend:** 🔵 Planned · 🟡 In progress · 🟢 Complete · 🔴 Blocked · ⚪ Superseded

FEATURE-014 is not in this table: its plan was dropped before review and lives only on a parked
branch. FEATURE-015 to FEATURE-024 come from the architecture review of 2026-11-22 and are numbered
in the order they are meant to land; each plan's Dependencies section says why.

## Branch conventions

- Integration branch: `feature/<feature-slug>/integration`, never `feature/<feature-slug>`.
- Task branch: `feature/<feature-slug>/<phase>.<step>-<short-desc>`, e.g.
  `feature/silver-rebuild/1.2-walk-table`.
- Plans live in `dev/plans/`, reviews in `dev/reviews/`, executable technical plans in
  `dev/plans/technical/`, post-implementation notes in `dev/plans/implementations/`.
