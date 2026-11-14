-- Completeness of Silver listings (FEATURE-005, task 2.1).
-- Per operation, overall and per property type:
--   listings        all listing rows
--   with_detail     listings with a detail record; the others were only seen in search results
--   nonnull_rate    for every attribute, the share of listings with a detail record that have it
-- And per operation, from observation: the share of empty search cards.
-- Aggregates only. Snapshot set of 2026-10-31.
WITH
listing_s AS (SELECT * FROM listing FOR VERSION AS OF 2497744844056595693),
observation_s AS (SELECT * FROM observation FOR VERSION AS OF 1037665036251468126),
grouped AS (
    SELECT
        coalesce(operation, '(none)') AS operation,
        CASE WHEN grouping(property_type) = 1 THEN 'all'
             ELSE coalesce(property_type, '(none)') END AS segment,
        count(*) AS listings,
        count(detail_observed_at) AS with_detail,
        ARRAY[
            'property_type',
            'price_eur',
            'prev_price_eur',
            'price_drop_pct',
            'community_fee_eur_month',
            'sqm_built',
            'sqm_usable',
            'sqm_plot',
            'rooms',
            'bathrooms',
            'floor',
            'storeys',
            'is_exterior',
            'orientation',
            'has_elevator_state',
            'has_balcony_state',
            'has_terrace_state',
            'has_pool_state',
            'has_garden_state',
            'has_storage_room_state',
            'has_parking',
            'parking_included_in_price',
            'has_ac_state',
            'has_fitted_wardrobes_state',
            'has_heating',
            'heating_scope',
            'heating_type',
            'neighborhood',
            'page_district',
            'city',
            'latitude',
            'longitude',
            'year_built',
            'condition',
            'energy_consumption_rating',
            'energy_consumption_kwh_m2',
            'energy_emissions_rating',
            'energy_emissions_kg_m2',
            'is_occupied'
        ] AS attributes,
        ARRAY[
            count(property_type),
            count(price_eur),
            count(prev_price_eur),
            count(price_drop_pct),
            count(community_fee_eur_month),
            count(sqm_built),
            count(sqm_usable),
            count(sqm_plot),
            count(rooms),
            count(bathrooms),
            count(floor),
            count(storeys),
            count(is_exterior),
            count(orientation),
            count(has_elevator_state),
            count(has_balcony_state),
            count(has_terrace_state),
            count(has_pool_state),
            count(has_garden_state),
            count(has_storage_room_state),
            count(has_parking),
            count(parking_included_in_price),
            count(has_ac_state),
            count(has_fitted_wardrobes_state),
            count(has_heating),
            count(heating_scope),
            count(heating_type),
            count(neighborhood),
            count(page_district),
            count(city),
            count(latitude),
            count(longitude),
            count(year_built),
            count(condition),
            count(energy_consumption_rating),
            count(energy_consumption_kwh_m2),
            count(energy_emissions_rating),
            count(energy_emissions_kg_m2),
            count(is_occupied)
        ] AS present
    FROM listing_s
    GROUP BY GROUPING SETS ((operation), (operation, property_type))
)
SELECT 'listings' AS metric, operation, segment, CAST(listings AS double) AS value FROM grouped
UNION ALL
SELECT 'with_detail', operation, segment, CAST(with_detail AS double) FROM grouped
UNION ALL
SELECT 'search_only', operation, segment, CAST(listings - with_detail AS double) FROM grouped
UNION ALL
SELECT 'nonnull_rate:' || attribute, operation, segment,
       round(CAST(n AS double) / nullif(with_detail, 0), 4)
FROM grouped CROSS JOIN UNNEST(attributes, present) AS a (attribute, n)
UNION ALL
SELECT 'empty_card_rate', coalesce(operation, '(none)'), 'all',
       round(avg(CASE WHEN is_empty_card THEN 1.0 ELSE 0.0 END), 4)
FROM observation_s
GROUP BY operation
ORDER BY metric, operation, segment
