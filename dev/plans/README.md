# Feature planning workflow

Feature plans for this repository, produced by the **Architect · Review · Implement** workflow
(see [`.claude/WORKFLOW.md`](../../.claude/WORKFLOW.md)). This file is the feature registry, not a
speculative roadmap: add a row only when its top-level plan exists.

## Registered features

| ID | Title | Status | Branch | Effort | Priority | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| FEATURE-001 | [Silver on Iceberg against landing/v1](FEATURE-001-silver-rebuild.md) | 🟢 Complete | `feature/silver-rebuild` | L (~16-20h) | High | architect |
| FEATURE-003 | [Incremental Silver from landing v2, run by AWS](FEATURE-003-incremental-silver.md) — superseded by ADR 0003 before implementation | ⚪ Superseded | `feature/incremental-silver` | L (~20-24h) | High | architect |
| FEATURE-004 | [Incremental Silver from landing v3, run by AWS](FEATURE-004-incremental-silver-from-landing-v3.md) | 🟢 Complete | `feature/incremental-silver-v3` | L (~28h) | High | architect |
| FEATURE-005 | [Silver data-quality exploration](FEATURE-005-silver-quality-exploration.md) | 🟡 In progress | `feature/silver-quality-exploration` | M (~10h) | High | architect |
| FEATURE-006 | [Redaction r2](FEATURE-006-redaction-r2.md) | 🟢 Complete | `feature/redaction-r2` | S (~3h) | High | architect |

**Status legend:** 🔵 Planned · 🟡 In progress · 🟢 Complete · 🔴 Blocked · ⚪ Superseded

## Branch conventions

- Integration branch: `feature/<feature-slug>/integration`, never `feature/<feature-slug>`.
- Task branch: `feature/<feature-slug>/<phase>.<step>-<short-desc>`, e.g.
  `feature/silver-rebuild/1.2-walk-table`.
- Plans live in `dev/plans/`, reviews in `dev/reviews/`, executable technical plans in
  `dev/plans/technical/`, post-implementation notes in `dev/plans/implementations/`.
