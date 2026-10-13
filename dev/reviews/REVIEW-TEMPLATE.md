# Review — FEATURE-XXX: <feature title>

**Reviewer:** `reviewer` · **Date:** YYYY-MM-DD · **Plan:** [FEATURE-XXX](../plans/FEATURE-XXX-<slug>.md)
**Verdict:** <✅ Approved | ⚠️ Changes Recommended | 🔄 Alternative Proposed | ❌ Major Revision Needed>

## Summary

Two or three sentences stating whether the proposed outcome is feasible, the most important reason
for the verdict, and what must happen next. Do not repeat the feature-plan summary.

## Evidence checked

- **Plan and dependencies:** <paths and current status checked>
- **Code, tests, data, and configuration:** <paths and exact commands with outcomes>
- **Evidence reviewed:** <commands, spikes, or artifacts reproduced; observed result and limitation>
- **Unverified assumptions:** <claim, why it was not verified, owner, and verification point / none>

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass/Fail/N/A | <reference or action> |
| Repository and dependency assumptions match reality | Pass/Fail/N/A | <reference or action> |
| Supporting evidence is reproducible and sufficient | Pass/Fail/N/A | <reference or action> |
| Design is the simplest adequate option | Pass/Fail/N/A | <reference or action> |
| TDD slices and validation commands are executable | Pass/Fail/N/A | <reference or action> |
| Data integrity, data sourcing, privacy, and security are addressed | Pass/Fail/N/A | <reference or action> |
| Operations, recovery, migration, and cost are addressed | Pass/Fail/N/A | <reference or action> |

Any failed correctness, data-integrity, legal, security, or core-outcome gate blocks approval. Use
`N/A` only with a short reason.

## Strengths

- <verified decision that should be preserved during revision or implementation>

Omit this section when there is no material strength worth preserving; do not invent praise.

## Findings

Each finding gets an ID, a severity, evidence, impact, and a concrete recommendation. Severity
drives the gate: 🔴 blocks approval, 🟡 requires an explicit disposition before approval, and 🟢 is
optional.

### 🔴 H1 — <title>

- **Problem:** <what's wrong>
- **Evidence:** <specific repository path, command result, or reproducible observation>
- **Impact:** <what happens if ignored>
- **Recommendation:** <specific, actionable fix>
- **Owner / verification:** <who resolves it and how the re-review confirms it>

### 🟡 M1 — <title>

- **Problem:** <description>
- **Evidence:** <specific repository path, command result, or reproducible observation>
- **Impact:** <trade-off or risk if unchanged>
- **Recommendation:** <suggested improvement>
- **Disposition required:** <fix / explicitly accept with rationale>

### 🟢 L1 — <title>

- **Evidence:** <specific observation>
- **Suggestion:** <optional improvement>
- **Benefit:** <why it may help; safe to skip without blocking>

Repeat only the finding blocks that exist. Do not keep placeholder findings in a completed review.

## Alternatives considered

- **<approach name>** — <how it differs>. Trade-off: <pros vs. cons>. Verdict: <use when… / stick
  with the plan because…>.

Use `None — no materially different option improves the stated outcome.` when an alternative would
be speculative.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| <description> | Low/Med/High | Low/Med/High | <preventive or recovery action> | <owner and observable warning> |

## Effort check

- **Plan estimate:** <S/M/L (~Xh)>
- **Reviewer estimate:** <S/M/L (~Yh)> — confidence <Low/Med/High>
- **Why it differs / hidden complexity:** <factors that move the number>

## Reuse & conflicts

- **Reuse:** `path/to/module` — <verified capability to reuse / none>
- **Conflict / coordinate with:** <changed file, branch, feature, or none>

## Technical-plan readiness

- **Artifact:** <`dev/plans/technical/FEATURE-XXX-technical-plan.yaml` / withheld>
- **Tasks:** <count; dependency graph and critical path verified yes/no>
- **TDD contract:** <RED failures and commands verified yes/no>
- **File boundaries:** <create/modify paths contained in allowed files; overlaps absent yes/no>
- **Git workflow:** <base, integration, task ancestry, and publish policy unambiguous yes/no>
- **Required checks:** <existing repository checks only>

Complete this section for an Approved verdict. For every other verdict, set the artifact to
`withheld` and name the missing readiness condition.

## Approval criteria

- **Blockers (must fix):** <list of 🔴 items>
- **Required dispositions:** <list of 🟡 items and accept/fix decision>
- **Optional:** <list of 🟢 items>
- **Accepted residual risk:** <explicitly accepted non-blocking risk, or none>
- **Technical plan:** <emitted only for Approved / withheld pending re-review>

## Next step

<For Approved only: "Run `/implementer FEATURE-XXX`." Otherwise name the decision owner,
required correction, and re-review step.>

Record post-implementation learning separately under `dev/plans/implementations/`; do not rewrite
the review decision after implementation.
