-- Plausibility of Silver values (FEATURE-005, task 2.2).
-- Over listings with a detail record, per operation, overall and per property type:
--   p<nn>:<column>  approximate percentiles (0.1, 1, 5, 25, 50, 75, 95, 99, 99.9) of price,
--                   areas, price per built m², rooms, bathrooms, year built and community fee
--   rule:<name>     how many listings break each candidate rule (thresholds in params)
-- Per operation: listings whose same-day search-result price differs from the detail price.
-- Aggregates only. Snapshot set of 2026-10-31.
WITH
listing_s AS (SELECT * FROM listing FOR VERSION AS OF 2497744844056595693),
observation_s AS (SELECT * FROM observation FOR VERSION AS OF 1037665036251468126),
params AS (
    SELECT
        10000 AS sale_price_min, 20000000 AS sale_price_max,
        100 AS rent_price_min, 30000 AS rent_price_max,
        10 AS sqm_min, 2000 AS sqm_max,
        300.0 AS sale_eur_m2_min, 15000.0 AS sale_eur_m2_max,
        2.0 AS rent_eur_m2_min, 60.0 AS rent_eur_m2_max,
        12 AS rooms_max,
        2 AS bathrooms_over_rooms_max,
        1500 AS year_min, 2027 AS year_max,
        0.5 AS price_drop_max,
        5000 AS sale_priced_like_rent, 50000 AS rent_priced_like_sale
),
detail AS (
    SELECT
        l.*,
        CAST(l.price_eur AS double) / nullif(l.sqm_built, 0) AS eur_m2,
        CASE l.operation WHEN 'rent' THEN p.rent_price_min ELSE p.sale_price_min END AS price_min,
        CASE l.operation WHEN 'rent' THEN p.rent_price_max ELSE p.sale_price_max END AS price_max,
        CASE l.operation WHEN 'rent' THEN p.rent_eur_m2_min ELSE p.sale_eur_m2_min END AS eur_m2_min,
        CASE l.operation WHEN 'rent' THEN p.rent_eur_m2_max ELSE p.sale_eur_m2_max END AS eur_m2_max,
        p.*
    FROM listing_s AS l CROSS JOIN params AS p
    WHERE l.detail_observed_at IS NOT NULL
),
grouped AS (
    SELECT
        operation,
        CASE WHEN grouping(property_type) = 1 THEN 'all'
             ELSE coalesce(property_type, '(none)') END AS segment,
        ARRAY['price_eur', 'sqm_built', 'sqm_usable', 'eur_m2', 'rooms', 'bathrooms',
              'year_built', 'community_fee_eur_month'] AS columns,
        ARRAY[
            approx_percentile(CAST(price_eur AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(CAST(sqm_built AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(CAST(sqm_usable AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(eur_m2, ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(CAST(rooms AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(CAST(bathrooms AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(CAST(year_built AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999]),
            approx_percentile(CAST(community_fee_eur_month AS double), ARRAY[0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999])
        ] AS percentiles,
        ARRAY['price_below_min', 'price_above_max', 'sqm_built_below_min', 'sqm_built_above_max',
              'eur_m2_below_min', 'eur_m2_above_max', 'usable_above_built',
              'no_rooms_outside_studio', 'rooms_above_max', 'bathrooms_above_rooms',
              'year_built_out_of_range', 'price_drop_above_max', 'sale_priced_like_rent',
              'rent_priced_like_sale'] AS rules,
        ARRAY[
            count_if(price_eur < price_min),
            count_if(price_eur > price_max),
            count_if(sqm_built < sqm_min),
            count_if(sqm_built > sqm_max),
            count_if(eur_m2 < eur_m2_min),
            count_if(eur_m2 > eur_m2_max),
            count_if(sqm_usable > sqm_built),
            count_if(rooms = 0 AND property_type <> 'studio'),
            count_if(rooms > rooms_max),
            count_if(bathrooms > rooms + bathrooms_over_rooms_max),
            count_if(year_built < year_min OR year_built > year_max),
            count_if(price_eur < prev_price_eur * (1 - price_drop_max)),
            count_if(operation = 'sale' AND price_eur < sale_priced_like_rent),
            count_if(operation = 'rent' AND price_eur > rent_priced_like_sale)
        ] AS breaks
    FROM detail
    GROUP BY GROUPING SETS ((operation), (operation, property_type))
),
labels AS (
    SELECT ARRAY['p00.1', 'p01', 'p05', 'p25', 'p50', 'p75', 'p95', 'p99', 'p99.9'] AS names
)
SELECT q.label || ':' || c.column_name AS metric, g.operation, g.segment, round(q.value, 2) AS value
FROM grouped AS g
CROSS JOIN labels
CROSS JOIN UNNEST(g.columns, g.percentiles) AS c (column_name, vals)
CROSS JOIN UNNEST(labels.names, c.vals) AS q (label, value)
UNION ALL
SELECT 'rule:' || r.rule, g.operation, g.segment, CAST(r.n AS double)
FROM grouped AS g CROSS JOIN UNNEST(g.rules, g.breaks) AS r (rule, n)
UNION ALL
SELECT 'search_price_differs_from_detail', l.operation, 'all', CAST(count_if(o.price_eur <> l.price_eur) AS double)
FROM listing_s AS l
JOIN observation_s AS o
    ON o.source = l.source AND o.source_listing_id = l.source_listing_id
    AND o.observed_date = l.observed_date
WHERE l.detail_observed_at IS NOT NULL
GROUP BY l.operation
UNION ALL
SELECT 'search_price_compared', l.operation, 'all', CAST(count(*) AS double)
FROM listing_s AS l
JOIN observation_s AS o
    ON o.source = l.source AND o.source_listing_id = l.source_listing_id
    AND o.observed_date = l.observed_date
WHERE l.detail_observed_at IS NOT NULL
GROUP BY l.operation
ORDER BY metric, operation, segment
