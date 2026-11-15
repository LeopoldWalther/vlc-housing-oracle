-- One row per listing with a detail record: one flag per plausibility rule of the data-quality
-- findings (docs/data-quality/silver-quality-2026-11.md). A flag is an inferred label beside the
-- facts of silver_listing and means "check", never "wrong".
--
-- A flag is true only when its inputs are present and break the rule; a missing input gives
-- false, never null, so a flag answers exactly "does this listing break the rule".
-- Rent listings are judged at the rent thresholds, every other operation at the sale thresholds.
-- Price per m² is compared as price against threshold × area, never as a division, and every
-- product is a BIGINT: a 32-bit product overflows on exactly the values these rules exist for.
WITH detail AS (
    SELECT
        *,
        coalesce(operation = 'rent', false) AS is_rent
    FROM silver_listing
    WHERE detail_observed_at IS NOT NULL
),
judged AS (
    SELECT
        *,
        IF(is_rent, getvariable('rent_price_min'), getvariable('sale_price_min')) AS price_min,
        IF(is_rent, getvariable('rent_price_max'), getvariable('sale_price_max')) AS price_max,
        IF(
            is_rent,
            getvariable('rent_price_per_sqm_min'),
            getvariable('sale_price_per_sqm_min')
        ) AS price_per_sqm_min,
        IF(
            is_rent,
            getvariable('rent_price_per_sqm_max'),
            getvariable('sale_price_per_sqm_max')
        ) AS price_per_sqm_max
    FROM detail
)
SELECT
    source,
    source_listing_id,
    coalesce(price_eur < price_min OR price_eur > price_max, false) AS price_out_of_bounds,
    coalesce(sqm_built < getvariable('sqm_built_min'), false) AS sqm_built_below_min,
    coalesce(sqm_built > getvariable('sqm_built_max'), false) AS sqm_built_above_max,
    coalesce(sqm_built > 0 AND price_eur < price_per_sqm_min * sqm_built::BIGINT, false)
        AS price_per_sqm_below_min,
    coalesce(sqm_built > 0 AND price_eur > price_per_sqm_max * sqm_built::BIGINT, false)
        AS price_per_sqm_above_max,
    coalesce(sqm_usable > sqm_built, false) AS sqm_usable_above_built,
    coalesce(rooms > getvariable('rooms_max'), false) AS rooms_above_max,
    coalesce(bathrooms > rooms::BIGINT + getvariable('bathrooms_over_rooms_max'), false)
        AS bathrooms_above_rooms,
    -- The upper bound follows the observation, not the run's clock, so a backfill gives the same
    -- flags: a listing may announce a building finished in the year after it was seen.
    coalesce(
        year_built < getvariable('year_built_min')
        OR year_built > year(detail_observed_at) + getvariable('year_built_ahead_max'),
        false
    ) AS year_built_out_of_range,
    -- A drop of more than half: twice the price is still below the previous price.
    coalesce(2 * price_eur::BIGINT < prev_price_eur, false) AS price_drop_above_max,
    getvariable('transformation_version') AS transformation_version
FROM judged
ORDER BY source, source_listing_id
