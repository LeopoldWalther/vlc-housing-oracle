# FEATURE-011 — Listing location from coordinates: municipality, district and census section

**Status:** 🔵 Planned · **Effort:** M (~10h) · **Priority:** Medium
**Branch root:** `feature/listing-location` · **Created:** 2026-11-16 · **Updated:** 2026-11-16

> Authored by `architect`. Reviewed by `reviewer` (see `dev/reviews/REVIEW-FEATURE-011.md`).
> Implemented by `implementer` from `dev/plans/technical/FEATURE-011-technical-plan.yaml`.

## Objective

Assign every listing with coordinates to its official census section, and with it to a district
and a municipality, in a Silver table `listing_location`. Outside the city no listing has a
district today, and the census section is the key under which public statistics (income,
population, reference rents) are published.

## Context

- **Current state:**
    - `listing` carries `municipality`, `district` and `neighborhood` as the portal names them,
        and `latitude` and `longitude` for 99.2% of the listings with a detail record.
    - `listing_quality` derives a municipality centre as a median of coordinates (FEATURE-008).
    - The lake holds landing, Silver and Gold. There is no place for public reference data, and
        the Silver job reads nothing but landing.
- **Problem** (aggregate counts on Silver of 2026-11-15):
    - all 9,693 listings outside the city have no `district`, and 9,349 of them no
        `neighborhood`; inside the city 191 of 13,466 lack a neighbourhood;
    - portal names are no key for public data; 3 of 84 neighbourhood names occur in more than
        one district.
- **Constraints:**
    - This repository never fetches source pages. Public reference data is a different input,
        and adding it is an architecture decision that needs its own record.
    - A Silver run must stay reproducible: the same landing and the same reference data give
        the same rows.
    - Tests use synthetic polygons, never the real boundary file.

## Scope

- **In scope:**
    - ADR 0004: reference data as immutable, versioned Parquet under `reference/v1/`;
    - a converter from the statistics office's census-section shapefile to that layout;
    - the Silver table `listing_location`;
    - the spatial extension in the image, read access to the reference prefix, and the task's
        new argument.
- **Out of scope:**
    - neighbourhood polygons of the city: its open-data service could not be queried in the
        planning session, and the portal's neighbourhood is missing for only 1.4% of city
        listings;
    - any statistic joined on the section (income, population, reference rents): later
        features on the same layer;
    - replacing the median centre of `listing_quality` with polygons;
    - downloading the file in code: the operator fetches it once a year.
- **Users / consumers:** every analysis by area outside the city; later joins with public
    statistics; the agent's area context.

## Dependencies

- **Needs:** FEATURE-004 and FEATURE-008 — complete. Implemented after FEATURE-009 and
    FEATURE-010, which change the same function.
- **Unblocks:** reference datasets keyed by census section.

## Open assumptions

- **Assumption:** the national census-section file is public, current and readable by DuckDB. —
    **Evidence:** spike of 2026-11-16: the 2026 edition (65 MB) downloads without a login;
    DuckDB's spatial extension reads it; it is in ETRS89 / UTM 30N and carries section,
    district, municipality and province codes and the municipality name; the province has 1,841
    sections in 266 municipalities, the city 588 sections in 19 districts. — **If wrong:** N/A,
    observed.
- **Assumption:** the point-in-polygon join is cheap and right. — **Evidence:** same spike:
    after reprojection to WGS84 a known public point in the city centre falls into the city's
    district 01; 23,000 random points join in 0.1 s; the province as Parquet is 2.9 MB. —
    **If wrong:** N/A, observed.
- **Assumption:** the extension can be baked into the ARM64 image. — **Evidence:** the
    extension file for DuckDB 1.5.6 on `linux_arm64` exists on the extension server; the image
    already installs two extensions at build time. Not built. — **If wrong:** task 3.1 fails at
    build, before any deployment.
- **Assumption:** the file's licence allows this use. — **Evidence:** unvalidated. — **If
    wrong:** task 1.1 records the licence before anything is stored; without a suitable licence
    the feature stops there.
- **Assumption:** nearly all coordinates fall into a section. — **Evidence:** no coordinate
    lies outside the province box (findings). Unmeasured per polygon. — **If wrong:** the codes
    are null and the first run reports how many.

## Design

- **Primary approach:**
    - **Reference layer (ADR 0004).** `reference/v1/<dataset>/vintage=<date>/part-0.parquet`
        with a `_manifest.json` beside it: source, licence, retrieval date, SHA-256 of the
        downloaded archive, row count. A vintage is written once and never replaced.
    - **Converter.** `python -m src.reference.census_sections --shapefile <path> --province 46
        --vintage <date> --out <dir>` reads a local file, keeps one province, reprojects to
        WGS84 and writes the Parquet and the manifest. The operator downloads the archive and
        uploads the result; no code touches the network.
    - **Silver.** The job gains `--census-sections <vintage path>`. With it, the run computes
        `listing_location` from Silver's `listing` and the polygons and replaces it whole, like
        `listing_quality`. Without it the table is not written, so local runs and existing
        tests are unaffected.
- **Contracts and invariants:**
    - Reference columns: `section_code` (10 digits), `district_code` (7), `municipality_code`
        (5), `municipality_name`, `geometry` (WKB, EPSG:4326).
    - `listing_location`: one row per listing with a detail record; `source`,
        `source_listing_id`, `municipality_code`, `district_code`, `section_code` (all null
        without coordinates or outside every polygon), `reference_vintage`,
        `transformation_version`.
    - A point inside two polygons takes the smaller section code, so the result is total.
    - A new vintage changes rows without a new `transformation_version`: the input changed, not
        the rule. `reference_vintage` records which input.
- **OOP / SOLID:** pure functions and SQL are sufficient; the converter is one function.
- **Patterns:** none.
- **Deliberately rejected complexity:**
    - a scheduled downloader with change detection: one file a year;
    - a generic "reference dataset" abstraction: one dataset exists;
    - a geometry library in Python: the DuckDB extension does read, reprojection and join.

## Approach

### Phase 1 — Reference data

- [ ] **1.1 ADR 0004 and the licence.** Record the decision, the layout and the file's licence
    and attribution. Validation: workflow check; the licence text is quoted in the ADR.
- [ ] **1.2 Synthetic polygons.** RED: `src/synthetic/reference.py` yields a small shapefile
    with squares around the synthetic municipalities in a projected system, and a listing's
    coordinate lies in exactly one. Fails because the generator does not exist.
- [ ] **1.3 Converter.** RED: converting the synthetic shapefile writes the five columns in
    WGS84, keeps only the requested province, and writes a manifest with the archive's hash;
    an existing vintage is refused. Fails because the command does not exist.

### Phase 2 — The table

- [ ] **2.1 The rule.** RED: a listing inside a square gets its three codes; one without
    coordinates and one outside every square get nulls; one inside two overlapping squares gets
    the smaller code; every listing with a detail record has one row.
- [ ] **2.2 Pipeline.** RED: a run with the argument writes the table and reports its snapshot;
    a run without it writes none; stepwise commits equal a backfill; a second vintage changes
    `reference_vintage`.

### Phase 3 — Operation

- [ ] **3.1 Image.** The spatial extension is installed at build time; the image test loads it
    without network.
- [ ] **3.2 Infrastructure.** Read access to `reference/v1/*` for the task role and the new
    argument from a variable; `terraform validate` and a plan that changes only the task role
    policy and the task definition.
- [ ] **3.3 Documentation.** The runbook: yearly procedure, the table and its reading.
- [ ] **3.4 First vintage and first run (operator, with approval).** Convert and upload the 2026
    edition, deploy, then counts only: listings with a section, listings whose portal
    municipality differs from the polygon's, sections and districts in use. Recorded in
    `dev/plans/implementations/FEATURE-011-listing-location.md`.

## Files

- **Create:** `docs/adr/0004-reference-data.md`, `src/reference/__init__.py`,
    `src/reference/census_sections.py`, `src/synthetic/reference.py`,
    `src/silver/sql/listing_location.sql`.
- **Change:** `src/silver/rebuild.py`, `Dockerfile`, `infra/silver/ecs.tf`,
    `infra/silver/variables.tf`, `docs/silver-rebuild.md`, `README.md`, `CLAUDE.md` (the
    reference layer in the architecture section).
- **Tests:** `tests/test_reference_census_sections.py`, `tests/test_silver_listing_location.py`,
    `tests/test_synthetic_reference.py` (new), `tests/test_silver_cli.py`.

## Test strategy

- **Unit:** conversion and reprojection, province filter, manifest, the join rule and its
    boundary cases.
- **Contract / fixtures:** column names and types of the reference file and the table; synthetic
    polygons.
- **Integration:** a local SQLite catalog with a backfill and stepwise commits, with and without
    the argument.
- **Retries / idempotency / evolution:** incremental equals backfill; a vintage is written
    once; a second vintage.
- **Configured quality checks:** `python -m unittest discover -s tests -t .`,
    `ruff check src tests dev && ruff format --check src tests dev`,
    `python dev/tools/check_leaks.py`, `python dev/tools/validate_workflow.py`,
    `terraform -chdir=infra/silver validate`, the image build.
- **Manual:** the conversion, upload and comparison of 3.4.

## Operational and compliance impact

- **Data sourcing / legal:** a public boundary file enters the lake under its licence, with
    attribution in the manifest and the ADR. Nothing of it enters the repository.
- **Privacy / security:** the table holds ids and area codes. The task role gains read access
    to one prefix.
- **Observability:** the report line; the first-run counts.
- **Failure and recovery:** a missing or unreadable vintage fails the run before any write, with
    the path in the message; its runs stay pending.
- **Migration / compatibility:** additive; no backfill.

## Estimated monthly cloud cost

| Component | Pricing basis | Assumption | Est. / month |
|---|---|---|---|
| S3 storage | $0.023 per GB | 3 MB per vintage | ~$0.00 |
| **Total (new AWS components)** | | | **~$0.00/month** |

- **Cost drivers & cheaper alternatives:** none of note; the join runs inside the existing task.
- **External / non-AWS costs:** none; the file is free.
- **Budget check:** yes.

## Success criteria

- [ ] ADR 0004 is accepted and names the licence.
- [ ] Every listing with a detail record has one row; a listing with coordinates inside the
    province has its three codes.
- [ ] A run without the argument behaves as today.
- [ ] Any sequence of commits and a backfill give the same rows for one vintage.
- [ ] Every behavior was implemented test-first and focused plus affected suites pass
- [ ] Configured lint, format, leak, infrastructure and workflow checks pass
- [ ] The yearly procedure and the table are documented
- [ ] No speculative scope, secrets, personal data, or generated clutter is introduced

## Open questions & risks

- **Question (user, at review):** is the census section the right first geography, or are the
    city's neighbourhood polygons needed in the same feature?
- **Question (user, before 3.4):** approve the upload, the role change and the deployment.
- **Risk:** the portal places a listing approximately, so it falls into a neighbouring section.
    — *Mitigation:* documented; sections are small, so statistics by district are safer than by
    section. The first run counts listings whose municipality differs.
- **Risk:** section boundaries change every year. — *Mitigation:* vintages are immutable and
    `reference_vintage` is on every row.

## Progress log

- **2026-11-16** — Plan created from the exploration of 2026-11-15; the third of four features.
    Spike: the boundary file was read, reprojected and joined with DuckDB.
