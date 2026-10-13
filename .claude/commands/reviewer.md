---
description: Stage 2 of the ARI workflow — gate a feature plan and emit its technical plan when approved.
argument-hint: FEATURE-XXX
---

Read `.claude/roles/reviewer.md` and act as the **Reviewer** for $ARGUMENTS. Stay in this
conversation: a verdict other than Approved returns a decision to the user, so do not delegate this
to a subagent, which cannot reach them.

Work the review process in the role definition: verify the plan's claims against repository
evidence, apply the review lenses and the assumption gate, and always write
`dev/reviews/REVIEW-FEATURE-XXX.md`. Emit
`dev/plans/technical/FEATURE-XXX-technical-plan.yaml` only on an Approved verdict.

Finish by reporting the verdict, the ranked findings, and who owns the next decision.
Do not implement, create branches, commit, or push.
