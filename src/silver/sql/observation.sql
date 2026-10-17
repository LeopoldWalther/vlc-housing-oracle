-- One row per listing and day. The portal pads a result page with listings from neighbouring
-- areas, so one listing-day can appear in several areas. The order is total (the boundary check
-- makes the key unique per area), so every rebuild keeps the same copy: a real card before padding,
-- a priced copy before an unpriced one, then alphabetically by area.
SELECT
    *,
    getvariable('transformation_version') AS transformation_version
FROM landing_observation
QUALIFY row_number() OVER (
    PARTITION BY source, source_listing_id, observed_date
    ORDER BY
        is_empty_card,
        price_eur DESC NULLS LAST,
        municipality,
        district NULLS FIRST,
        operation,
        observed_at,
        ingested_at NULLS LAST
) = 1
ORDER BY source_listing_id, observed_date
