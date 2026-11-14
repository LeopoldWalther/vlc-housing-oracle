-- Rule pg1: two listings with a detail record are certainly the same property when
--   * operation, municipality, rooms, bathrooms and sqm_built are equal and none is null;
--   * the higher price_eur is at most price_gap_max_percent above the lower, in integers;
--   * both have coordinates less than distance_max_meters apart (haversine, sphere of 6,371 km);
--   * floor: if either condition is 'new', both floors are present and equal; otherwise they are
--     equal or at least one is missing. A null condition counts as not new.
-- The source is not compared. Each pair comes once, smaller (source, source_listing_id) first.
-- Evidence: docs/data-quality/silver-quality-2026-11.md (29 of 29 judgeable pairs were the same
-- property; every relaxation tested was uncertain).
CREATE OR REPLACE TEMP MACRO haversine_meters(lat_a, lon_a, lat_b, lon_b) AS
    2 * 6371000 * asin(sqrt(
        pow(sin(radians(lat_b - lat_a) / 2), 2)
        + cos(radians(lat_a)) * cos(radians(lat_b)) * pow(sin(radians(lon_b - lon_a) / 2), 2)
    ));

SELECT
    a.source AS source_a,
    a.source_listing_id AS source_listing_id_a,
    b.source AS source_b,
    b.source_listing_id AS source_listing_id_b
FROM gold_listing AS a
JOIN gold_listing AS b
    ON a.operation = b.operation
    AND a.municipality = b.municipality
    AND a.rooms = b.rooms
    AND a.bathrooms = b.bathrooms
    AND a.sqm_built = b.sqm_built
    AND (a.source, a.source_listing_id) < (b.source, b.source_listing_id)
WHERE a.detail_observed_at IS NOT NULL
    AND b.detail_observed_at IS NOT NULL
    AND 100 * greatest(a.price_eur, b.price_eur)::BIGINT
        <= (100 + getvariable('price_gap_max_percent')) * least(a.price_eur, b.price_eur)::BIGINT
    AND haversine_meters(a.latitude, a.longitude, b.latitude, b.longitude)
        < getvariable('distance_max_meters')
    AND CASE
        WHEN coalesce(a.condition = 'new', false) OR coalesce(b.condition = 'new', false)
            THEN coalesce(a.floor = b.floor, false)
        ELSE a.floor IS NULL OR b.floor IS NULL OR a.floor = b.floor
    END
ORDER BY source_a, source_listing_id_a, source_b, source_listing_id_b;
