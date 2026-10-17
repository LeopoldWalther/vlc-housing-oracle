# VLC Housing Oracle - Project Instructions

## Mission and Scope

Turn listing snapshots of the Valencia housing market into a trustworthy, historized analytics
platform: Silver tables maintained incrementally from a documented landing contract, Gold features and aggregates,
and models on top. Data acquisition lives in a separate private repository and ends at the landing
contract; nothing in this repository fetches, parses, or stores source pages.

Current candidate use cases are:

1. fair-price estimation and over-/under-pricing detection;
2. fix-and-flip renovation arbitrage;
3. buy-to-let yield maps;
4. listing liquidity and time-on-market prediction;
5. market-dynamics and gentrification analysis.

Treat these as roadmap options, not settled requirements. Keep data models reusable until a use case
is explicitly selected.

## Ground Truth and Priorities

- Write every repository artifact in English: code, comments, docstrings, documentation, plans,
    reviews, configuration, and commit messages. Chat with the user may be in another language, but
    nothing written into the repository is.
- This repository is evolving. Inspect the current code, tests, plans, and configuration before
    changing anything; do not describe planned components as already implemented.
- Resolve conflicts in this order: correctness and data integrity, privacy, simplicity and
    readability, maintainability, then performance.
- Make the smallest coherent change that satisfies the requirement. Do not add speculative
    features, generic frameworks, compatibility layers, or abstractions for hypothetical reuse.
- Preserve public contracts unless the task explicitly changes them. State assumptions when the
    repository does not establish a decision.

## Target Architecture

Keep dependencies directed through these boundaries:

1. **Landing** (input, owned elsewhere): Parquet tables under `landing/v<n>/` whose schema is
     `contracts/landing/v<n>/schema.json`. This repository reads landing and never writes it.
2. **Silver** validates, deduplicates, and historizes listings as **Apache Iceberg** tables,
     maintained incrementally from landing by pure DuckDB transformations: each run replaces the
     day partitions a landing manifest names and records that manifest in its snapshots; a full
     rebuild is a deliberate backfill (see [ADR 0001](docs/adr/0001-silver-on-iceberg.md) and
     [ADR 0002](docs/adr/0002-incremental-processing.md)).
3. **Gold** publishes use-case-specific features, aggregates, and training datasets as plain
     Parquet, so a browser can read them over HTTP.
4. **Consumers** (notebooks, models, a web front end) query curated data only.

Every derived record must be traceable to its source, listing identifier, observation time,
ingestion time, and transformation version. A training dataset records the Silver snapshot it was
built from.

## Mandatory TDD Workflow

Use red-green-refactor for every behavior change:

1. **Red:** add or change the smallest test that specifies the behavior, then run it and confirm it
     fails for the expected reason. For a bug, first add a regression test that reproduces it.
2. **Green:** write only enough production code to pass that test.
3. **Refactor:** improve names and structure only while tests stay green.
4. Run the narrowest relevant test after each edit, then the affected suite before finishing.

Do not write production behavior first and backfill tests. If no test harness exists, establish the smallest suitable harness as the first slice. Tests must be deterministic, behavior-focused, and independent of execution order. Mock or fake network, clock, randomness, object storage, catalog, and other paid or unreliable boundaries in unit tests. Use small synthetic fixtures and controlled local resources for integration tests; never read real landing data in the default test suite. Test outcomes and contracts, not private implementation details. Cover the happy path, malformed or missing source fields, boundary values, retries/idempotency, and schema evolution where relevant. Do not weaken assertions or delete coverage merely to make a test pass.

## Simple, Clean Code

- Prefer the simplest readable implementation. A short pure function is better than a class when no state, lifecycle, interchangeable behavior, or dependency boundary exists.
- Give each module, class, and function one clear responsibility. Keep functions short enough to understand at a glance, but do not split cohesive logic to satisfy an arbitrary line count.
- Use domain names such as `listing_snapshot`, `asking_price`, and `observed_at`; avoid vague names such as `data`, `manager`, `helper`, or `utils` when a precise name is available.
- Use explicit types at public and architectural boundaries. Avoid `Any` and unstructured dictionaries for stable domain records. Prefer immutable value objects for validated records.
- Write docstrings for public APIs and non-obvious contracts. Do not repeat the signature in prose. Comments explain why a decision or invariant exists, never narrate obvious code.
- Validate at system boundaries and fail with specific, actionable exceptions. Never silently drop malformed records or catch broad exceptions without adding context and preserving the cause.
- Pass configuration explicitly. Keep secrets out of source code, fixtures, logs, and notebooks.
- Add a dependency only when the standard library or an existing dependency cannot solve the need clearly. Explain and test the new boundary.
- Remove duplication only after the shared concept is clear. Three obvious lines are often better than a premature abstraction.

## OOP, SOLID, and Design Patterns

Use OOP and SOLID as design tests, not as requirements to maximize classes or interfaces.

- **Encapsulation:** objects protect real invariants and expose small, intention-revealing APIs.
- **Abstraction:** introduce an interface at an external boundary or where multiple implementations actually exist or are required by the current task.
- **Inheritance:** use only for a genuine substitutable is-a relationship. Prefer composition.
- **Polymorphism:** use it when callers must treat real variants uniformly, such as table sinks.
- **SRP:** separate fetching, parsing, normalization, persistence, orchestration, and analytics.
- **OCP/LSP:** add variants through stable contracts and keep every implementation behaviorally substitutable; do not distort a model merely to claim extensibility.
- **ISP:** expose narrow consumer-specific protocols rather than broad service interfaces.
- **DIP:** core logic depends on domain-level protocols; inject network, clock, storage, and model-serving implementations at the composition root.

Apply a named pattern only when it removes present duplication or coupling. Likely useful patterns are **Strategy** for genuinely variable sinks or validation, **Repository** for table-backed access, and **Dependency Injection** at external boundaries. Use a Factory or Template Method only when concrete variants already justify it. Never add inheritance, an interface, or a pattern solely to demonstrate a principle.

## Data and Lakehouse Rules

- Define and test table schemas deliberately. The landing contract is identified by its schema
    file's SHA-256, pinned in a test; a change is a new `landing/v<n>`, never an edit.
- Process incrementally by day partition: replace whole partitions, never append row by row, so a
    rerun is harmless. Store a run's checkpoint with its output (landing manifest, Iceberg snapshot
    property), not in a separate state store. A rule change carries a new `transformation_version`
    and is applied to history by an explicit full backfill; it never silently rewrites history. Gold is Parquet under versioned
    prefixes.
- Preserve temporal meaning: distinguish source publication/update time, observation time, and
    ingestion time; store timestamps in UTC and convert only at presentation boundaries.
- Represent currency and measurements without binary floating-point surprises. Record currency and
    units explicitly; normalize only with documented rules.
- Treat listing disappearance as an observation-derived state, not proof of a completed sale or
    rental. Keep facts separate from inferred labels.
- Version transformations and ML datasets. Prevent target leakage by respecting observation time in
    training and evaluation splits.
- Minimize personal data. Free text is redacted before it enters Silver, under a recorded redaction
    version; no raw personal information in logs, fixtures, analytics tables, or model features.

## Data Sourcing and Publication

- This repository is public. It holds code, contracts, synthetic data, and documentation, never
    real listing data, listing identifiers, URLs, descriptions, bucket names, or account identifiers.
- Tests and examples run on the synthetic landing generator under `src/synthetic/`. Real data is read
    only by an operator, from a private location passed as configuration.
- Published results are aggregates. Anything that would let a reader reconstruct individual listings
    stays private.
- The leak guard in CI must stay green; never weaken it to make a change pass.

## Infrastructure and Operations

- Define cloud resources as code under `infra/` when they first appear; keep secrets and
    account-specific values out of the repository and pass them as configuration.
- Any feature that changes cloud resources documents expected monthly cost, assumptions, dominant
    cost drivers, and a cheaper alternative.
- Notebooks are for exploration, not production pipelines. Move reusable, tested behavior into
    importable modules under `src/`.

## Repository Workflow

- For non-trivial features, use the Architect -> Reviewer -> Implementer workflow documented in
    `.claude/WORKFLOW.md` and `dev/plans/README.md`. Plans belong in `dev/plans/`, reviews in
    `dev/reviews/`, and executable technical plans in `dev/plans/technical/`.
- Build each implementation task as an independently testable TDD slice. Keep changes focused; do
    not mix feature work with unrelated refactors or formatting.
- Follow the branch conventions in `dev/plans/README.md`. Do not create branches, commit, push, or
    change feature status unless the user requests it or the active workflow requires it.
- Discover commands from repository configuration instead of inventing them. After changing workflow
    artifacts, run `python dev/tools/validate_workflow.py`.

## Definition of Done

A change is complete only when:

- the requested behavior is covered by a test that failed first and now passes;
- focused tests and the affected broader suite pass;
- linting and formatting pass;
- data contracts, operational behavior, and costs are documented when affected;
- no secrets, real data, generated clutter, or unrelated refactors are included;
- the final response reports what changed, the exact validation performed, and any remaining risk.
