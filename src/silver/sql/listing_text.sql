-- One row per listing with a detail record: the free text of the record listing chose, with
-- personal data redacted from the description. Titles, location and features stay verbatim. The
-- boundary check guarantees every detail record has exactly one text row.
SELECT
    text.source,
    text.source_listing_id,
    text.observed_at,
    text.ingested_at,
    text.title,
    text.location,
    redact_personal(text.description) AS description,
    text.features,
    getvariable('redaction_version') AS redaction_version,
    getvariable('transformation_version') AS transformation_version
FROM chosen_detail AS detail
JOIN newest_listing_text AS text
    ON text.source = detail.source
    AND text.run_id = detail.run_id
    AND text.area = detail.area
    AND text.source_listing_id = detail.source_listing_id
    AND text.observed_at = detail.observed_at
ORDER BY text.source_listing_id
