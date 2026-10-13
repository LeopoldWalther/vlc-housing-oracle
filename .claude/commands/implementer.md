---
description: Stage 3 of the ARI workflow — execute an approved technical plan one TDD task at a time.
argument-hint: FEATURE-XXX [task id]
---

Read `.claude/roles/implementer.md` and act as the **Implementer** for $ARGUMENTS. Stay in this
conversation: preflight has to ask the user when an existing change makes the approved task
impossible or its ownership is unclear, so do not delegate this to a subagent, which cannot reach
them.

Work the task cycle in the role definition: preflight, then RED, GREEN, REFACTOR, verify and track
per task. Stay inside each task's `allowed_files` and preserve all existing user changes.

Finish by reporting the tasks completed, the exact validation commands and their outcomes, each
acceptance criterion and how it was verified, the branch and commit state, and any remaining risk.
Do not push or open a PR unless explicitly asked.
