# FEATURE-XXX — <short title>

**Status:** 🔵 Planned · **Effort:** <S/M/L (~Xh)> · **Priority:** <High/Medium/Low>
**Branch root:** `feature/<feature-slug>` · **Created:** YYYY-MM-DD · **Updated:** YYYY-MM-DD

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-XXX.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-XXX-technical-plan.yaml`.

## Objective

One or two sentences describing the observable outcome and why it matters now.

## Context

- **Current state:** <what exists today, with links to code, tests, data, or prior plans>
- **Problem:** <the verified gap or risk this feature addresses>
- **Constraints:** <legal, data, compatibility, budget, or operational limits>

## Scope

- **In scope:** <the smallest coherent capability delivered by this feature>
- **Out of scope:** <adjacent capabilities deliberately deferred>
- **Users / consumers:** <who or what observes the result>

## Dependencies

- **Needs:** FEATURE-YYY — <reason>
- **Unblocks:** FEATURE-ZZZ — <reason>

## Open assumptions

> List what the plan depends on but could not confirm from the repository. For each one, state how
> it was validated or how implementation will falsify it. Delete the section when the plan rests on
> nothing uncertain. No particular validation format is required — use the cheapest one that
> produces evidence (command, throwaway script, or notebook).

- **Assumption:** <claim> — **Evidence:** <what was run and observed, or "unvalidated"> —
  **If wrong:** <consequence and fallback>

## Design

- **Primary approach:** <the simplest solution and why it fits the current repository>
- **Boundaries and data flow:** <components touched and dependency direction>
- **Contracts and invariants:** <inputs, outputs, schema, provenance, timestamps, idempotency, or N/A>
- **OOP / SOLID:** <only the objects or interfaces justified by state, invariants, variants, or an
	external boundary; otherwise state that pure functions/direct composition are sufficient>
- **Patterns:** <named pattern and present problem it solves, or "None">
- **Deliberately rejected complexity:** <abstractions, dependencies, or compatibility work not
	needed for the stated scope>

## Approach

Outline the solution as ordered phases. Tests are part of each behavior task, never a final testing
phase. Every behavior task states its RED test and expected failure, minimum GREEN behavior, and
REFACTOR/verification step. Non-behavior tasks use the cheapest validation-first equivalent.

### Phase 1 — <first independently verifiable capability>
- [ ] <RED → GREEN → REFACTOR action and observable outcome>

### Phase 2 — <next capability or integration boundary>
- [ ] <RED → GREEN → REFACTOR action and observable outcome>

### Phase 3 — <operational completion, migration, or documentation>
- [ ] <validation-first action and observable outcome>

## Files

- **Create:** `path/to/new_file` — <purpose>
- **Change:** `path/to/existing_file` — <what changes and why>
- **Tests:** `path/to/test_file` — <what it covers>

## Test strategy

- **Unit:** <contracts, happy paths, boundary values, and malformed inputs>
- **Contract / fixtures:** <small recorded or synthetic inputs and schema-drift cases>
- **Integration:** <cross-component flow using controlled local or explicitly gated resources>
- **Retries / idempotency / evolution:** <relevant repeat-run and schema/partition cases, or N/A>
- **Configured quality checks:** <exact test, type, lint, format, and validation commands that exist>
- **Manual (if any):** <what to check by hand>

## Operational and compliance impact

- **Data sourcing / legal:** <what crosses the landing contract, published data, or N/A>
- **Privacy / security:** <personal-data handling, secrets, IAM, encryption, or N/A>
- **Observability:** <logs, metrics, drift/data-quality signals, or N/A>
- **Failure and recovery:** <timeouts, bounded retries, quarantine, replay, rollback, or N/A>
- **Migration / compatibility:** <schema or deployment transition, or N/A>

## Estimated monthly cloud cost

> Keep this section. For no cloud impact, state `$0/month — no resources added or changed` and omit
> the table. Otherwise use current pricing assumptions and include every changed service.

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| <service> | <unit price> | <usage assumption> | ~$<x> |
| **Total (new AWS components)** | | | **~$<x>/month** |

- **Cost drivers & cheaper alternatives:** <what dominates the bill and how to reduce it>
- **External / non-AWS costs:** <e.g. third-party SaaS, billed separately>
- **Budget check:** <within the project's monthly target? yes/no>

## Success criteria

- [ ] <measurable outcome 1>
- [ ] <measurable outcome 2>
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured type, lint, format, infrastructure, and workflow checks pass
- [ ] Configured coverage threshold is met, if one exists
- [ ] Data, operational, migration, and cost contracts are documented where affected
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question:** <decision still needed, owner, and deadline/decision point>
- **Risk:** <what could go wrong> — *Mitigation:* <how we reduce it>
- **Assumption:** <claim, supporting evidence, and how implementation can falsify it>

## Progress log

- **YYYY-MM-DD** — <note about progress, blockers, or decisions>
