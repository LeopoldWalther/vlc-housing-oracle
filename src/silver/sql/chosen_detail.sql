-- The newest detail record of each listing. One listing can hold detail records in several areas
-- and on several days; the order is total, so listing and listing_text pick the same record on
-- every rebuild.
SELECT *
FROM newest_listing_detail
QUALIFY row_number() OVER (
    PARTITION BY source, source_listing_id
    ORDER BY
        observed_at DESC,
        ingested_at DESC NULLS LAST,
        municipality,
        district NULLS FIRST,
        operation
) = 1
