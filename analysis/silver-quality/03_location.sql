-- Location quality of Silver listings (FEATURE-005, task 2.3).
-- Over listings with a detail record, per operation:
--   with_coordinates             listings with latitude and longitude
--   decimals:<n>                 listings whose latitude has n decimal places (precision)
--   outside_province             coordinates outside a box around the province (params)
--   shared_points / listings_on_shared_points / max_listings_per_point
--                                exact coordinate pairs used by more than one listing
--   km_from_municipality:p<nn>   distance from the municipality's median coordinate
--   beyond_km:<n>                listings farther than n km from it
--   district_differs_from_page_district   both present and different after normalising spelling
-- And overall: neighbourhood names that appear in more than one district.
-- Aggregates only. Snapshot set of 2026-10-31.
WITH
listing_s AS (SELECT * FROM listing FOR VERSION AS OF 2497744844056595693),
params AS (
    SELECT 38.6 AS lat_min, 40.8 AS lat_max, -1.6 AS lon_min, 0.6 AS lon_max,
           111.2 AS km_per_degree
),
detail AS (
    SELECT * FROM listing_s WHERE detail_observed_at IS NOT NULL
),
located AS (
    SELECT
        d.*,
        length(regexp_extract(regexp_replace(format('%.8f', d.latitude), '0+$', ''), '\.(\d*)$', 1))
            AS decimals
    FROM detail AS d
    WHERE d.latitude IS NOT NULL AND d.longitude IS NOT NULL
),
centres AS (
    SELECT municipality,
           approx_percentile(latitude, 0.5) AS lat_c,
           approx_percentile(longitude, 0.5) AS lon_c
    FROM located
    GROUP BY municipality
),
distances AS (
    SELECT
        l.operation,
        p.km_per_degree * sqrt(
            power(l.latitude - c.lat_c, 2)
            + power((l.longitude - c.lon_c) * cos(radians(c.lat_c)), 2)
        ) AS km
    FROM located AS l
    JOIN centres AS c ON c.municipality = l.municipality
    CROSS JOIN params AS p
),
points AS (
    SELECT operation, latitude, longitude, count(*) AS n
    FROM located
    GROUP BY operation, latitude, longitude
)
SELECT 'with_coordinates' AS metric, operation, 'all' AS segment, CAST(count(*) AS double) AS value
FROM located GROUP BY operation
UNION ALL
SELECT 'without_coordinates', operation, 'all', CAST(count_if(latitude IS NULL OR longitude IS NULL) AS double)
FROM detail GROUP BY operation
UNION ALL
SELECT 'decimals:' || CAST(decimals AS varchar), operation, 'all', CAST(count(*) AS double)
FROM located GROUP BY operation, decimals
UNION ALL
SELECT 'outside_province', l.operation, 'all',
       CAST(count_if(l.latitude NOT BETWEEN p.lat_min AND p.lat_max
                     OR l.longitude NOT BETWEEN p.lon_min AND p.lon_max) AS double)
FROM located AS l CROSS JOIN params AS p GROUP BY l.operation
UNION ALL
SELECT 'shared_points', operation, 'all', CAST(count_if(n > 1) AS double) FROM points GROUP BY operation
UNION ALL
SELECT 'listings_on_shared_points', operation, 'all', CAST(sum(IF(n > 1, n, 0)) AS double)
FROM points GROUP BY operation
UNION ALL
SELECT 'max_listings_per_point', operation, 'all', CAST(max(n) AS double) FROM points GROUP BY operation
UNION ALL
SELECT 'km_from_municipality:' || q.label, d.operation, 'all', round(q.km, 2)
FROM (
    SELECT operation, approx_percentile(km, ARRAY[0.5, 0.9, 0.99, 0.999]) AS kms
    FROM distances GROUP BY operation
) AS d
CROSS JOIN UNNEST(ARRAY['p50', 'p90', 'p99', 'p99.9'], d.kms) AS q (label, km)
UNION ALL
SELECT 'beyond_km:' || CAST(t.km AS varchar), d.operation, 'all', CAST(count_if(d.km > t.km) AS double)
FROM distances AS d CROSS JOIN UNNEST(ARRAY[5, 10, 20]) AS t (km)
GROUP BY d.operation, t.km
UNION ALL
-- district is a slug from the search path and page_district the page's name for it, so both are
-- compared without accents, case and separators.
SELECT 'district_differs_from_page_district', operation, 'all',
       CAST(count_if(
           regexp_replace(lower(translate(district, 'áéíóúàèòïüñç', 'aeiouaeoiunc')), '[^a-z0-9]', '')
           <> regexp_replace(lower(translate(page_district, 'áéíóúàèòïüñç', 'aeiouaeoiunc')), '[^a-z0-9]', '')
       ) AS double)
FROM detail GROUP BY operation
UNION ALL
SELECT 'district_and_page_district_present', operation, 'all',
       CAST(count_if(district IS NOT NULL AND page_district IS NOT NULL) AS double)
FROM detail GROUP BY operation
UNION ALL
SELECT 'neighborhoods_in_several_districts', 'all', 'all', CAST(count(*) AS double)
FROM (
    SELECT neighborhood FROM detail
    WHERE neighborhood IS NOT NULL AND district IS NOT NULL
    GROUP BY neighborhood HAVING count(DISTINCT district) > 1
)
UNION ALL
SELECT 'neighborhoods', 'all', 'all', CAST(count(DISTINCT neighborhood) AS double) FROM detail
ORDER BY metric, operation, segment
