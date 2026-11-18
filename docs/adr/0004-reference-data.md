# ADR 0004 — Reference data as immutable, versioned vintages

**Status:** Accepted · **Date:** 2026-11-18 · **Amends:** [ADR 0001](0001-silver-on-iceberg.md)

## Context

Until now Silver had one input: landing, the listing snapshots collected by the acquisition side.
Assigning a listing to its census section needs a second kind of input, public data that someone
else publishes: the boundaries of the census sections, issued once a year by the national
statistics office (Instituto Nacional de Estadística, INE). More datasets of that kind will follow
(income, population and reference rents are published per section).

Public data differs from landing in three ways:

- it is not collected by a run and has no commit: it appears as a file on a website, about once a
    year, and is replaced there by the next edition;
- its publisher sets the terms of use, so every copy needs its source and licence beside it;
- it changes Silver's rows without any change of rule: next year's boundaries move some listings
    into another section.

Silver must stay reproducible: the same landing and the same reference data give the same rows,
today and in a year. A Silver run that read "the current file" from the publisher's website would
break that, and it would put network access to a third party into a job that has none.

This repository never fetches source pages. That rule is about listing data and stays as it is.
Reference data is a different input, and this record is the decision to admit it.

## Decision

1. **A reference dataset is stored as immutable vintages.** One edition of a dataset is one
    directory, written once and never replaced:

    ```text
    reference/v1/<dataset>/vintage=<date>/part-0.parquet
    reference/v1/<dataset>/vintage=<date>/_manifest.json
    ```

    `<date>` is the reference date the publisher gives the edition. `v1` is the version of this
    layout. A corrected edition of the same date is a new vintage with a later date, not an
    overwrite. The converter refuses a vintage directory that exists.

2. **A manifest lies beside the data** and says where it came from. It is a JSON object with
    `dataset`, `vintage`, `source` (the address the file was downloaded from), `licence`,
    `retrieved` (the day of the download), `archive_sha256` (the SHA-256 of the downloaded file),
    `province`, `crs` (the coordinate system of the downloaded file) and `rows`.

3. **The Parquet file describes itself.** Every row carries `vintage`, so a reader takes the
    vintage from the data and not from a path or an argument. For the census sections the columns
    are `section_code` (10 digits), `district_code` (7), `municipality_code` (5),
    `municipality_name`, `geometry` (WKB in EPSG:4326, longitude before latitude) and `vintage`,
    ordered by `section_code`.

4. **A command of this repository converts; an operator downloads and uploads.**
    `python -m src.reference.census_sections` reads the archive the operator downloaded, keeps one
    province, reprojects to WGS84 and writes the vintage directory with its manifest. The operator
    uploads that directory. No code of this repository touches the network for reference data.

5. **Silver is told which vintage to read.** The job takes the vintage directory as an argument
    (`--census-sections`), checks the file before its first write and records the vintage on every
    derived row (`reference_vintage`). Without the argument the table that needs it is not written.

6. **A new vintage is not a rule change.** It changes rows without a new `transformation_version`:
    the input changed, and `reference_vintage` says which input a row was derived from. Tables
    derived from reference data are replaced whole in every run, so pointing the job at a new
    vintage takes effect with the next run and needs no backfill.

7. **The real file never enters this repository**, a fixture or a test. Tests run on synthetic
    polygons with province codes that no real province has.

## Licence

Read on 2026-11-18 in the publisher's legal notice (*Aviso legal*, section "Reutilización de la
información contenida en este sitio web"). The passages that govern this use, quoted:

> La licencia de uso general a aplicar a la información estadística de este sitio web, salvo que
> se indique lo contrario, es la Creative Commons Reconocimiento 4.0 (CC BY 4.0) que implica la
> autorización para la reutilización de la información en condiciones no restrictivas, siendo
> posible la copia, distribución y comunicación pública, así como la producción de obras
> derivadas, incluso con finalidad comercial citando la autoría.

> Debe citarse la fuente de la información objeto de reutilización. Esta cita podrá realizarse de
> la siguiente manera: Fuente: Sitio web del INE: www.ine.es si no se realiza ningún tratamiento
> de los datos o bien: Elaboración propia con datos extraídos del sitio web del INE: www.ine.es
> en caso de que se realice tratamiento de los datos.

> Debe mencionarse la fecha de la última actualización de la información objeto de reutilización,
> siempre y cuando estuviera incluida en el original.

> No se podrá indicar, insinuar o sugerir que el INE participa, patrocina o apoya la reutilización
> que se lleve a cabo con la información.

In short: reuse, also of derived works and also commercial, is allowed with attribution (CC BY
4.0); the edition's date is named where the original gives one; nothing may suggest that the
publisher takes part in or endorses the reuse. The publisher reserves the right to change these
terms, so the notice is read again with every new vintage.

What follows for this repository:

- **Attribution.** The data is processed (filtered, reprojected), so the line is the second one:
    *Elaboración propia con datos extraídos del sitio web del INE: www.ine.es*. It is stored in the
    manifest's `licence` field together with the licence's name, and it accompanies every
    published result that rests on the boundaries.
- **Date.** The edition's reference date is the vintage, stored in the manifest and on every row.
- **One inconsistency, recorded.** The page that offers the file carries a footer image whose
    text names CC BY-SA 4.0, while the image's link and the legal notice name CC BY 4.0. The
    legal notice is taken as authoritative. The difference would matter only for publishing the
    boundaries themselves or a work derived from them, which this repository does not do: results
    are aggregates, and the vintage stays in the private lake.

## Consequences

- A manual step once a year: download the new edition, convert it, upload it, point the job at
    it. The runbook holds the procedure.
- The lake gains a third prefix beside landing and Silver, and the Silver task's role gains read
    access to it.
- A year's change of boundaries is visible and reversible: both vintages stay, and
    `reference_vintage` on every row says which one a table was derived from.
- Further datasets keyed by census section join on `section_code` of the same vintage and follow
    the same layout.
- DuckDB's spatial extension becomes part of the environment: CI, the image and a developer's
    machine install it once; nothing installs it at run time.

## Alternatives considered

- **A scheduled downloader with change detection.** It would put network access to a third party
    and a parser of that party's website into a job, for one file a year whose address changes
    with every edition. Rejected; the operator's download is recorded in the manifest.
- **A generic "reference dataset" abstraction** (a registry of datasets, a common converter
    interface). One dataset exists. The layout above is the shared part; a second dataset will
    show what else is common.
- **A geometry library in Python** (reading shapefiles, reprojection and the join in Python).
    DuckDB's spatial extension does all three inside the engine that already runs every Silver
    rule, without a new Python dependency.
- **Point-in-polygon in plain SQL**, without an extension. It would mean implementing the
    geometry test and the reprojection from the publisher's projected system by hand, with every
    border case; rejected for a maintained implementation.
- **Reference data as Iceberg tables in Silver.** A vintage is written once and read whole; it
    needs no snapshots, and a plain Parquet file with a manifest can be checked by hand.
