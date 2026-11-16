# Review — FEATURE-011: Listing location from coordinates

**Reviewer:** `reviewer` · **Date:** 2026-11-16 · **Plan:** [FEATURE-011](../plans/FEATURE-011-listing-location.md)
**Verdict:** ✅ Approved

## Summary

The plan is feasible, and the review could settle its last unmeasured assumption: on real
coordinates the join assigns 22,961 of 22,974 listings to exactly one census section. The
design keeps Silver reproducible, because the boundary file enters as an immutable, versioned
input and the table records which one it used. Four corrections were needed:

- the tests need DuckDB's spatial extension, which neither CI nor the README installs, and the
    "image test" the plan relied on does not exist;
- the manifest was to hold the hash of an archive the converter never saw;
- where `reference_vintage` comes from, and when a bad file is noticed, were open;
- the axis order of the reprojection and the border of a polygon were not fixed.

They are folded into the plan. The user answered the open question: the census section is the
first geography.

## Evidence checked

- **Plan and dependencies:** `dev/plans/FEATURE-011-listing-location.md` (🔵 Planned,
    registered). FEATURE-004 and FEATURE-008 are 🟢 Complete. FEATURE-009 is implemented and in
    review as a pull request; FEATURE-010 is approved and not implemented.
- **Code, tests, data, and configuration:**
    - `src/silver/rebuild.py`: `QUALITY_LISTING_COLUMNS` already holds `detail_observed_at`,
        `latitude` and `longitude`, so the single read of `listing` for the label rules covers
        this rule; `listing_property_group` shows how a table outside `SILVER_TABLES` is
        written in the same call.
    - `Dockerfile`: `httpfs` and `aws` are installed at build time as the job's user; nothing
        loads an extension during the build.
    - `.github/workflows/ci.yml`: dependencies come from `pip` alone, and its header says every
        command is the one a developer runs locally. `.github/workflows/image.yml` builds the
        image and runs nothing in it.
    - `src/synthetic/landing.py`: three listings lie within 100 m of one point, one lies 15 km
        north of it, and most have no coordinates.
- **Evidence reviewed:**
    - **The file, reproduced.** The 2026 edition downloads without a login (65 MB). It holds
        one shapefile in a sub-folder of the archive, in ETRS89 / UTM 30N, with the fields
        `CUSEC`, `CUDIS`, `CUMUN`, `CPRO` and `NMUN` among others. The province has 1,841
        sections (1,806 polygons and 35 multipolygons) in 266 municipalities; the city has 588
        sections in 19 districts. As the plan says.
    - **DuckDB 1.5.6 with the spatial extension, locally.** It reads the shapefile inside the
        archive through a `/vsizip/` path that names the inner file; the archive's root alone is
        not readable. It writes a shapefile with a projected system, which the synthetic
        generator needs. Without `LOAD spatial` a spatial function fails with a catalog error.
        A point with latitude and longitude swapped joins nothing.
    - **The join on real coordinates,** privately, counts only, nothing stored: 23,159 listings
        with a detail record, 22,974 with coordinates; 22,961 fall into one section, 13 into
        none, none into two. They use 1,100 sections in 87 districts of 50 municipalities. The
        join takes 0.1 s. 515 listings lie in another municipality than most listings of their
        portal municipality: the table adds information the portal's name does not carry.
    - **Licence.** A web search found the statistics office's general licence to be CC BY 4.0
        with attribution. The current legal notice was not read.
- **Unverified assumptions:**
    - The licence's current wording. Owner: Implementer. Verification point: task 1.1, before
        anything is stored; the feature stops there otherwise.
    - The extension builds into the ARM64 image. Owner: Implementer. Verification point: task
        3.1, where the image build of the pull request fails otherwise.

## Gate result

| Gate | Result | Evidence / required action |
| --- | --- | --- |
| Outcome and scope are measurable and bounded | Pass | One table, one dataset, one command |
| Repository and dependency assumptions match reality | Pass | After M1 |
| Supporting evidence is reproducible and sufficient | Pass | File, join and counts reproduced |
| Design is the simplest adequate option | Pass | No downloader, no abstraction, no geometry library |
| TDD slices and validation commands are executable | Pass | After M1 and M4 |
| Data integrity, data sourcing, privacy, and security are addressed | Pass | After M2 and M3; licence gated by task 1.1 |
| Operations, recovery, migration, and cost are addressed | Pass | After M3; $0 |

## Strengths

- Reference data becomes a layer with its own ADR before the first dataset uses it.
- A vintage is immutable and named on every row, so a boundary change is visible and never
  silent.
- A run without the argument behaves as today, so the feature can be merged before it is
  switched on.
- The licence is checked before the first byte is stored.

## Findings

### 🟡 M1 — The tests need an extension nobody installs

- **Problem:** the converter, the synthetic polygons and the rule need DuckDB's spatial
    extension in every test run.
- **Evidence:** CI installs `pip` packages only; the README's development commands likewise;
    a spatial function without the extension loaded is a catalog error. The plan's check, "the
    image test loads it without network", refers to a test that does not exist.
- **Impact:** the first pull request fails in CI, or the tests skip silently.
- **Recommendation:** CI and the README gain one command that installs the extension, as they
    install packages. Tests do not skip. The image build installs the extension and loads it
    once as the job's user, so a missing extension fails the build.
- **Disposition required:** fixed in the feature plan and in tasks 1.2 and 3.1.

### 🟡 M2 — The manifest's hash had no source

- **Problem:** the converter read `--shapefile`, and the manifest recorded the SHA-256 of the
    downloaded archive.
- **Evidence:** a shapefile is several files; the archive holds it in a sub-folder.
- **Impact:** a hash typed by hand, or none: the provenance the manifest exists for would be
    unverified.
- **Recommendation:** the converter takes the archive, finds the one shapefile in it, reads it
    in place and hashes the archive. An archive with no or several shapefiles is refused. The
    source system is an argument, never an assumption.
- **Disposition required:** fixed in the feature plan and in tasks 1.2 and 1.3.

### 🟡 M3 — The vintage and the time of failure

- **Problem:** `reference_vintage` had no stated source, and "fails before any write" did not
    fit a table computed after the day partitions are written.
- **Evidence:** `listing_quality` and its siblings are computed from Silver after this run's
    days and listings are committed.
- **Impact:** a vintage parsed from a path, and a run that fails half-way with part of its
    work committed.
- **Recommendation:** the file carries `vintage` as a column. A run given the argument reads
    and checks the file before its first write and keeps the polygons for the rule.
- **Disposition required:** fixed in the feature plan and in tasks 1.3, 2.1 and 2.2.

### 🟡 M4 — Axis order and the border

- **Problem:** the plan said "reprojects to WGS84" and "inside".
- **Evidence:** the reprojection swaps the axes unless it is told to keep longitude first; the
    review's swapped point joined nothing. A point on a shared border is inside neither
    neighbour.
- **Impact:** every code null, which the null-tolerant contract would not flag; or a rare
    listing without a section although it lies on the map.
- **Recommendation:** both reprojections keep longitude before latitude; a point on a border
    belongs to the polygon, and the smaller code decides between two. The synthetic polygons
    are not symmetric in the two axes, so a swap fails a test.
- **Disposition required:** fixed in the feature plan and in tasks 1.2, 1.3 and 2.1.

### 🟢 L1 — Counts for the first run

- **Evidence:** the review's counts above.
- **Suggestion:** task 3.4 compares with them. Applied.
- **Benefit:** a deviation is noticed on the first run.

### 🟢 L2 — Names beside the codes

- **Evidence:** the table holds codes; the municipality's name is in the reference file only.
- **Suggestion:** left as planned. `listing.municipality` holds the portal's name, and a
    dataset of names keyed by code belongs to the features that join statistics.
- **Benefit:** the table stays a pure key table.

## Alternatives considered

- **Point-in-polygon in plain SQL over polygon edges** — no extension in the image, CI or
    tests. Trade-off: hand-written geometry for multipolygons and holes, where a proven library
    exists. Correctness outranks simplicity here. Verdict: stick with the plan.
- **Neighbourhood polygons of the city in the same feature** — closer to how people name
    places. Trade-off: a second dataset with its own licence and layout, for a city where 98.6%
    of listings already carry a neighbourhood. Verdict: later, as the user decided.
- **Reading the vintage from the path** — one column less. Trade-off: a renamed folder changes
    the data's meaning. Verdict: the column.

## Risks

| Risk | Likelihood | Impact | Mitigation | Owner / signal |
| --- | --- | --- | --- | --- |
| The portal's coordinate is approximate | High | Low | Documented; district-level statistics are safer than section-level ones | Consumer; documentation |
| Boundaries change every year | High | Low | Immutable vintages; `reference_vintage` on every row | Operator; yearly procedure |
| The licence does not allow the use | Low | High | Task 1.1 stops the feature before anything is stored | Implementer |
| The extension does not build on ARM64 | Low | Med | The image build of the pull request fails | CI |
| A later edition renames a field | Low | Low | The converter refuses the file and names the field | Operator |

## Effort check

- **Plan estimate:** M (~10h)
- **Reviewer estimate:** M (~11h) — confidence High
- **Why it differs / hidden complexity:** the archive handling and the test environment.

## Reuse & conflicts

- **Reuse:** the single read of `listing` for the label rules; the write path of
    `listing_property_group`; the build-time extension install of the `Dockerfile`; the
    injected clients and exit codes of `main()`.
- **Conflict / coordinate with:** FEATURE-009 and FEATURE-010 change `process_landing`; this
    feature starts from `main` after FEATURE-010 is merged. FEATURE-012 follows and adds ADR
    0005 and a second optional input in the same way.

## Technical-plan readiness

- **Artifact:** `dev/plans/technical/FEATURE-011-technical-plan.yaml`
- **Tasks:** 9; dependency graph and critical path verified yes
- **TDD contract:** RED failures and commands verified yes
- **File boundaries:** create/modify paths contained in allowed files; overlaps absent yes
- **Git workflow:** base, integration, task ancestry, and publish policy unambiguous yes
- **Required checks:** `python-lint-and-test`, `terraform-validate`, `workflow-consistency`

## Approval criteria

- **Blockers (must fix):** none
- **Required dispositions:** M1 to M4 fixed in the feature plan and the technical plan
- **Optional:** L1 applied; L2 left as planned
- **Accepted residual risk:** the licence's wording until task 1.1; the ARM64 build until task
    3.1
- **Technical plan:** emitted

## Next step

Run `/implementer FEATURE-011` after FEATURE-010 is merged to `main`.
