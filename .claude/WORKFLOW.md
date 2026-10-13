# Architect · Review · Implement — the agent workflow

This repository uses a small, three-stage workflow to take a feature from idea to merged code with
Claude Code. Each stage is a dedicated role with one job and a clear handoff.

```
 idea ──▶  /architect      ──▶  /reviewer  ──▶  /implementer  ──▶  PR
            (plan + proof)      (gate)          (build)
```

| Stage | Agent | Produces | Lives in |
| --- | --- | --- | --- |
| **Architect** | [`/architect`](commands/architect.md) · [role](roles/architect.md) | Feature plan | `dev/plans/FEATURE-XXX-<slug>.md` |
| **Review** | [`/reviewer`](commands/reviewer.md) · [role](roles/reviewer.md) | Review; technical plan when approved | `dev/reviews/REVIEW-FEATURE-XXX.md` and, after approval, `dev/plans/technical/FEATURE-XXX-technical-plan.yaml` |
| **Implement** | [`/implementer`](commands/implementer.md) · [role](roles/implementer.md) | Tested code, clean commits | feature branches → PR |

## How a feature flows

1. **Architect** — `/architect <goal>`. The Architect explores the codebase, asks
   clarifying questions, and writes a `FEATURE-XXX` plan plus a row in
   [`dev/plans/README.md`](../dev/plans/README.md). Where an assumption cannot be settled from the
   repository, the Architect gathers evidence with the cheapest suitable spike and records the
   result in the plan. A spike never replaces production TDD.
2. **Review** — `/reviewer FEATURE-XXX`. The Reviewer verifies the plan and its evidence
   against the repository and always writes a review. Only an approved review emits the technical
   plan with atomic implementation tasks; other verdicts return a concrete correction or decision
   to the Architect or user.
3. **Implement** — `/implementer FEATURE-XXX`. The Implementer works the technical plan
   task by task: a branch and a TDD cycle (RED → GREEN → REFACTOR) each, one commit per task,
   status kept in sync.
4. **Ship** — push the branch and open a PR referencing the feature ID.

## The review artifacts

An approved handoff from Review to Implement is deliberately split:

- The **review** (`dev/reviews/REVIEW-FEATURE-XXX.md`) explains *why* — verdict, ranked findings,
  risks, effort estimate. Read it for context.
- The **technical plan** (`dev/plans/technical/FEATURE-XXX-technical-plan.yaml`) says *what to do* —
  atomic tasks with acceptance criteria, branch names, commit messages, and file boundaries. This
  is what the Implementer executes.

For a non-approved verdict, the review is the only required artifact and there is no Implementer
handoff. This prevents the presence of a technical plan from implying approval while blockers remain.

## Why these are commands, not subagents

Each stage is a slash command in `.claude/commands/` that loads its role definition from
`.claude/roles/` into the running conversation. They are deliberately **not** `.claude/agents/`
subagents: a subagent runs in an isolated context and returns one report to the main assistant, so
it can never reach the user. All three stages need to — the Architect asks focused questions, the
Reviewer returns a non-approved verdict as a decision, and the Implementer's preflight asks when an
existing change makes the approved task impossible. Putting them in `.claude/agents/` would also
let Claude Code auto-delegate to them, which silently removes the user from the workflow.

## Conventions

- **Branches** are hierarchical: `feature/<feature-slug>/<phase>.<step>-<short-desc>`. A feature
  that needs an integration branch uses `feature/<feature-slug>/integration` — never
  `feature/<feature-slug>` itself, which git cannot create while task refs exist below it.
- **Status legend:** 🔵 planned · 🟡 in progress · 🟢 complete · 🔴 blocked.
- **Source of truth:** the technical-plan YAML drives task progress; the top-level feature file and
  the plans README mirror it.
- **Commits:** one focused commit per task, using the message from the technical plan.

## Keeping the artifacts consistent

A small checker keeps statuses, feature tables, and CI gate names aligned across the plan, review,
and technical-plan files. Run it before opening a PR:

```bash
python dev/tools/validate_workflow.py
```

A CI job that runs it on any change under `.claude/` or `dev/` is planned but not yet present
in `.github/workflows/`.

## Adapting this workflow to another project

The agents read project-specific conventions from
[`CLAUDE.md`](../CLAUDE.md). To reuse this workflow elsewhere, update that
file with the new project's overview, stack, and coding standards — the agents pick the rest up
automatically. Start the templates from
[`dev/plans/FEATURE-TEMPLATE.md`](../dev/plans/FEATURE-TEMPLATE.md) and
[`dev/reviews/REVIEW-TEMPLATE.md`](../dev/reviews/REVIEW-TEMPLATE.md).
