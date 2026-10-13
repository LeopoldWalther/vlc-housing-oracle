---
description: Stage 1 of the ARI workflow — turn a feature idea into a reviewable FEATURE-XXX plan.
argument-hint: <the feature, problem, and desired outcome>
---

Read `.claude/roles/architect.md` and act as the **Architect** for this task. Stay in this
conversation: the Architect asks the user focused questions, so do not delegate this to a subagent,
which cannot reach the user.

The feature to plan:

$ARGUMENTS

Work the process in the role definition: establish ground truth from the repository, ask only the
questions the repository cannot answer, settle open assumptions with the cheapest suitable
evidence, shape the smallest solution, slice it into TDD tasks, and write
`dev/plans/FEATURE-XXX-<slug>.md` plus its row in `dev/plans/README.md`.

Finish by reporting the plan path, the assumptions that remain open, and the handoff command.
Do not create branches, commit, or push.
