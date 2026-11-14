# Review — FEATURE-007: Property groups: one property behind several listings

**Reviewer:** `reviewer` · **Date:** 2026-11-14 · **Plan:** [FEATURE-007](../plans/FEATURE-007-property-groups.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible and turns a rule that exists only as prose into a small, tested dataset.
The pair conditions behave as stated in DuckDB, and the groups measured on real Silver are
plausible. Five corrections were needed, none of which changes the product: the plan
contradicted itself on the source, the recorded snapshot was not guaranteed to be the one read,
the way of writing to the lake was left open, group-level precision was an unowned assumption,
and the synthetic data cannot produce a pair today. All are folded in. The user chose Gold on
2026-11-14. The next step is implementation.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-007-property-groups.md` (🔵 Planned, registered,
    uncommitted on `main`); FEATURE-004 🟢 Complete; FEATURE-005 🟢 Complete and merged
    (`1fc6c5b`), so `docs/data-quality/silver-quality-2026-11.md` is on `main`.
- **Code, tests, data, and configuration:**
    - No `src/gold/` exists. ADR 0001 fixes Gold as plain Parquet under versioned prefixes;
        `CLAUDE.md` requires a dataset to record the Silver snapshot it was built from and keeps
        listing-level data private.
    - `src/silver/sql/listing.sql`: `listing` carries every column the rule reads (`operation`,
        `municipality`, `rooms`, `bathrooms`, `sqm_built`, `price_eur`, `latitude`, `longitude`,
        `floor`, `condition`, `detail_observed_at`), taken from the newest detail record.
    - `src/synthetic/landing.py`: `_detail` sets no coordinates, floor or condition, so every
        synthetic listing has null coordinates.
    - `requirements.txt`: `pyiceberg[pyarrow,…]` brings PyArrow with its file systems; no new
        dependency is needed.
    - The findings document states the rule this plan implements, criterion by criterion.
- **Evidence reviewed:**
    - **Pair conditions, reproduced in DuckDB 1.5.6 on six hand-written rows:** a haversine macro
        gives 333.6 m for 0.003° of latitude and 355.8 m for 0.0032°, so both sides of 350 m are
        testable; `100 × higher ≤ 105 × lower` pairs 100,000 with 105,000 and not with 105,001;
        the floor condition pairs resale with a missing floor and refuses a new build with one.
        The same rows contain a chain (A–B, B–C, not A–C), which shows the rule is not transitive.
        This proves the conditions, not the tests.
    - **Writing, reproduced:** `pyarrow.fs.FileSystem.from_uri` returns a local file system for a
        directory and an S3 file system with the path for an `s3://` location, and a Parquet
        file written through it reads back.
    - **Groups on real Silver:** the architect's counts (5,158 pairs, 1,832 groups over 4,849
        listings, 187 incomplete, largest 19) were produced in this session from an anonymised
        pair list that was deleted afterwards. They are taken as stated; the operator run of
        task 4.2 repeats them with the production code.
- **Unverified assumptions:** the precision of groups larger than two (M4). Owner: user;
    verification point: a privately judged sample of large groups, if a consumer depends on them.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass after correction | M1 removes a contradiction about the source. |
| Repository and dependency assumptions match reality | Pass after correction | M5: no synthetic listing can pair today. FEATURE-005 is merged. |
| Supporting evidence is reproducible and sufficient | Pass | Conditions and writing reproduced; real counts repeated by task 4.2. |
| Design is the simplest adequate option | Pass | One module, pure functions, no Gold framework; M3 settles the writer without a new dependency. |
| TDD slices and validation commands are executable | Pass | Every task fails first on a missing function or a missing row; commands exist in CI. |
| Data integrity, data sourcing, privacy, and security are addressed | Pass after correction | M2: the recorded snapshot is the one scanned. The dataset is private and holds no text, price or coordinate. |
| Operations, recovery, migration, and cost are addressed | Pass | Additive, hand-run, $0; one approval-gated write. |

## Strengths

- Thresholds are tested on both sides, and missing values never create a pair.
- Every listing gets a group id, so a consumer counts properties with one `COUNT(DISTINCT …)`.
- Chains are flagged, not hidden and not "repaired" by a rule nobody has evidence for.
- The plan measured the exact effect that the findings could only bound.

## Findings

### 🟡 M1 — The plan said both that the source is not a criterion and that cross-source pairs are out of scope

- **Problem:** the Design lists `source` under "not criteria", the Scope excluded "pairs across
    sources", and the group id was "the smallest `source_listing_id`", which is ambiguous as soon
    as two sources hold the same id string.
- **Evidence:** the plan's Scope and Design sections; the findings document: "Not criteria: the
    source".
- **Impact:** the Implementer would have to decide whether the join includes `source`.
- **Recommendation (applied):** the rule ignores the source, as the findings say. What is out of
    scope is validating such pairs. The group id is the `<source>:<source_listing_id>` of the
    smallest member ordered by `(source, source_listing_id)`.
- **Disposition required:** fixed in the plan and in the technical plan's conventions.

### 🟡 M2 — The recorded snapshot was not guaranteed to be the snapshot read

- **Problem:** the plan read "the current snapshot" and recorded its id, and relied on the
    operator running the build "while no Silver task runs".
- **Evidence:** Silver commits `listing` several times a day; `table.scan()` without an id reads
    whatever is current at that moment.
- **Impact:** a dataset could name one snapshot and hold the rows of another, which breaks the
    one provenance guarantee the dataset has.
- **Recommendation (applied):** take `current_snapshot().snapshot_id` once and scan with
    `snapshot_id=` that value. The operator precondition is dropped.
- **Disposition required:** fixed in the plan and in task 3.1.

### 🟡 M3 — How the file reaches the lake, and what "exactly 5%" means, were left to the Implementer

- **Problem:**
    - The command takes "a directory or an `s3://` location" but the plan did not say how it
        writes. DuckDB's `COPY` to S3 needs extensions that are preinstalled only in the Silver
        image, not on the operator's laptop.
    - "At most 5% above the lower" with floating point decides the boundary case by rounding.
    - The versioned prefix that ADR 0001 requires was not placed.
- **Evidence:** `src/silver/rebuild.py::_read_s3_through_duckdb` and the `Dockerfile`; the
    reproduced PyArrow file-system check; ADR 0001.
- **Impact:** a build that works in tests and fails at the first real run, or a boundary test
    that passes on one machine only.
- **Recommendation (applied):** write with PyArrow's file system from the `--out` location; make
    the versioned prefix part of `--out`, proposed as `gold/v1`; compare prices in integers.
- **Disposition required:** fixed in the plan and in the conventions. The prefix is confirmed by
    the user with the approval of task 4.2.

### 🟡 M4 — Group-level precision is an assumption without evidence

- **Problem:** the rule's evidence is 29 judged pairs. A group of 19 is 171 potential pairs, and
    nobody judged a group. The plan named this but gave it no disposition.
- **Evidence:** the plan's third open assumption ("unvalidated"); the findings' "the sample
    sizes are small".
- **Impact:** a consumer that trusts large groups could collapse a development into one
    property. The effect is bounded: 63 of 1,832 groups have six or more listings.
- **Recommendation (applied):** accept it as a stated limit. The documentation says that groups
    of more than two listings are not manually validated, the command reports how many groups
    exceed five listings, and `group_size` and `group_is_complete` let a consumer cap what it
    trusts. A privately judged sample of large groups is the follow-up if a consumer needs them.
- **Disposition required:** explicitly accepted with rationale; recorded in the plan, task 3.3
    and task 4.1.

### 🟡 M5 — No synthetic listing can pair today

- **Problem:** task 3.2 expected the build over the synthetic landing to find "exactly that
    group", but did not say that the generator's detail records have no coordinates, floor or
    condition, and that `_detail` takes no overrides for them.
- **Evidence:** `src/synthetic/landing.py`, `_detail`.
- **Impact:** an Implementer following the plan literally would write a scenario that never
    pairs, or change defaults for every synthetic listing and disturb the Silver tests.
- **Recommendation (applied):** `_detail` accepts attribute overrides and only the new scenario
    uses them. Because every other synthetic listing lacks coordinates, the test can assert one
    group of two and every other listing alone.
- **Disposition required:** fixed in the plan and in task 3.2.

### 🟢 L1 — A hand-run build is the right size for now

- **Evidence:** nothing reads the dataset yet; the Silver image's entry point is the Silver
    command, so automating the build means a second entry point or a second step in the task.
- **Suggestion:** keep the build manual, as planned, and automate it with the first consumer.
- **Benefit:** no unattended job without a reader.

### 🟢 L2 — Say in the README that this Gold dataset is not for the browser

- **Evidence:** `README.md:30` says Gold is "readable by a browser over HTTP"; this dataset is
    listing-level and private.
- **Suggestion:** the documentation task states that listing-level Gold datasets stay private
    and only aggregates derived from them are published.
- **Benefit:** prevents the first Gold dataset from setting the wrong precedent.

## Alternatives considered

- **A Silver table recomputed with every run** — automatic at once. Trade-off: an inferred label
    among the facts. Verdict: decided against by the user on 2026-11-14.
- **Cliques instead of connected components** — every group complete by construction.
    Trade-off: a listing can belong to several cliques, so the result is no partition without an
    arbitrary tie-break. Verdict: stick with the plan; the flag carries the same information.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| A large group is a development, not one property | Medium | Medium | Documented limit, `group_size`, the reported count of large groups | consumers; the count of groups above five listings |
| The group id changes between builds | Certain | Low | Snapshot id on every row; documentation: join within one build | consumers |
| A good third of true duplicates is missed | Certain | Medium | Stated; a "possible" tier needs its own evidence | architect of a follow-up |
| One-room homes cannot pair until the upstream defect is fixed | Certain | Low | Stated; the rule never pairs on missing data | acquisition side |
| The first write under a new prefix goes to the wrong place | Low | Low | Explicit approval and the prefix named before task 4.2 | user |

## Effort check

- **Plan estimate:** M (~10h)
- **Reviewer estimate:** M (~10h) — confidence Medium
- **Why it differs / hidden complexity:** unchanged. The synthetic scenario touches a generator
    every Silver test reads, and the build test needs a backfilled catalog.

## Reuse & conflicts

- **Reuse:** `NAMESPACE` from `src/silver/rebuild.py`; the SQLite-catalog fixture pattern of
    `tests/test_silver_rebuild.py`; `process_landing` to backfill the test catalog.
- **Conflict / coordinate with:**
    - FEATURE-002 changes `src/silver/rebuild.py` and its tests; this feature only imports from
        it. The two features share no production file except `README.md` and
        `dev/plans/README.md`, each a small conflict for whichever merges later.
    - A generator scenario added here changes the rows of every Silver table in the synthetic
        set; the equality tests compare against an oracle and are unaffected.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-007-technical-plan.yaml`
- **Tasks:** 10. The graph is acyclic; the critical path is
    1.1 → 1.2 → 1.3 → 2.1 → 2.2 → 3.1 → 3.2 → 3.3 → 4.1 → 4.2.
- **TDD contract:** RED failures and commands verified yes.
- **File boundaries:** create and modify paths lie inside the allowed files; none overlaps a
    forbidden path.
- **Git workflow:** an integration branch from `main` holds the plan, review and technical plan;
    tasks branch from it in order; one pull request; task 4.2 records its note on its own branch.
- **Required checks:** `python-lint-and-test`, `workflow-consistency`.

## Approval criteria

- **Blockers (must fix):** none.
- **Required dispositions:** M1, M2, M3 and M5 fixed; M4 explicitly accepted and documented.
- **Optional:** L1 adopted; L2 adopted in task 4.1.
- **Accepted residual risk:** unvalidated precision of groups larger than two; missed duplicates;
    a group id that is stable only within one build.
- **Technical plan:** emitted.

## Next step

Run `/implementer FEATURE-007`.
