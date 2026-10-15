-- One row per listing with a detail record: the free text of the record listing chose. The boundary
-- check guarantees every detail record has exactly one text row.
SELECT
    text.source,
    text.source_listing_id,
    text.observed_at,
    text.ingested_at,
    text.title,
    text.location,
    text.description,
    text.features,
    getvariable('transformation_version') AS transformation_version
FROM chosen_detail AS detail
JOIN landing_listing_text AS text
    ON text.source = detail.source
    AND text.area = detail.area
    AND text.source_listing_id = detail.source_listing_id
    AND text.observed_at = detail.observed_at
ORDER BY text.source_listing_id
