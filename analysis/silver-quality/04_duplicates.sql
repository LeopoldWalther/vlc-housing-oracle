-- Duplicate listings across ids (FEATURE-005, task 2.4).
-- A pair is two distinct listing ids of the same operation and municipality with equal built
-- area, rooms and bathrooms (tier A0); the tiers narrow it (see README.md):
--   A     equal asking price           A±10 / A±5   prices within 10% / 5% of the lower one
--   B     A±5 and within the distance threshold     C   B and an equal, non-null floor
-- Per operation and tier (segment):
--   pairs, concurrent_pairs, sequential_pairs   sequential only when the later listing was first
--                                               seen after RAMP_UP_END; first_sweep_sequential
--                                               counts the others
--   listings_in_pairs                          listings in at least one pair
--   same_text_pairs, same_point_pairs           identical redacted description / exact coordinates
--   surplus_min, surplus_max                   bounds on listings minus distinct properties:
--                                               pairs need not be transitive, so with V listings
--                                               in E pairs the surplus lies in [V/2, min(E, V-1)]
-- Tier A is an exact key, so it is also reported as clusters: clusters, cluster_size:<n>, surplus.
-- Within A0 pairs: the price gap's percentiles and the pairs within a gap of 0, 1, 5, 10, 20%.
-- Aggregates only. Snapshot set of 2026-10-31.
WITH
listing_s AS (SELECT * FROM listing FOR VERSION AS OF 2497744844056595693),
listing_text_s AS (SELECT * FROM listing_text FOR VERSION AS OF 4512740065342526121),
params AS (
    SELECT DATE '2026-09-30' AS ramp_up_end, 0.003 AS near_lat, 0.004 AS near_lon
),
candidates AS (
    SELECT
        l.source, l.source_listing_id, l.operation, l.municipality, l.sqm_built, l.rooms,
        l.bathrooms, l.price_eur, l.floor, l.latitude, l.longitude, l.first_seen, l.last_seen,
        CASE WHEN t.description IS NOT NULL
             THEN to_hex(md5(to_utf8(lower(trim(t.description))))) END AS text_hash
    FROM listing_s AS l
    LEFT JOIN listing_text_s AS t
        ON t.source = l.source AND t.source_listing_id = l.source_listing_id
    WHERE l.detail_observed_at IS NOT NULL
),
pairs AS (
    SELECT
        a.operation,
        a.source_listing_id AS a_id,
        b.source_listing_id AS b_id,
        CAST(abs(a.price_eur - b.price_eur) AS double) / least(a.price_eur, b.price_eur) AS gap,
        abs(a.latitude - b.latitude) < p.near_lat AND abs(a.longitude - b.longitude) < p.near_lon
            AS near,
        coalesce(a.floor = b.floor, false) AS same_floor,
        a.first_seen <= b.last_seen AND b.first_seen <= a.last_seen AS concurrent,
        greatest(a.first_seen, b.first_seen) > p.ramp_up_end AS later_after_ramp_up,
        coalesce(a.text_hash = b.text_hash, false) AS same_text,
        coalesce(a.latitude = b.latitude AND a.longitude = b.longitude, false) AS same_point
    FROM candidates AS a
    JOIN candidates AS b
        ON a.source = b.source
        AND a.operation = b.operation AND a.municipality = b.municipality
        AND a.sqm_built = b.sqm_built AND a.rooms = b.rooms AND a.bathrooms = b.bathrooms
        AND a.source_listing_id < b.source_listing_id
    CROSS JOIN params AS p
),
tiered AS (
    SELECT pairs.*, t.tier
    FROM pairs
    CROSS JOIN UNNEST(
        ARRAY['A0', 'A', 'A±10', 'A±5', 'B', 'C'],
        ARRAY[
            true,
            gap = 0,
            gap <= 0.10,
            gap <= 0.05,
            gap <= 0.05 AND coalesce(near, false),
            gap <= 0.05 AND coalesce(near, false) AND same_floor
        ]
    ) AS t (tier, member)
    WHERE t.member
),
per_tier AS (
    SELECT
        operation, tier,
        count(*) AS pairs,
        count_if(concurrent) AS concurrent_pairs,
        count_if(NOT concurrent AND later_after_ramp_up) AS sequential_pairs,
        count_if(NOT concurrent AND NOT later_after_ramp_up) AS first_sweep_sequential,
        count_if(same_text) AS same_text_pairs,
        count_if(same_point) AS same_point_pairs
    FROM tiered
    GROUP BY operation, tier
),
listings_per_tier AS (
    SELECT operation, tier, count(DISTINCT listing_id) AS listings_in_pairs
    FROM tiered CROSS JOIN UNNEST(ARRAY[a_id, b_id]) AS u (listing_id)
    GROUP BY operation, tier
),
tier_a_clusters AS (
    SELECT operation, count(*) AS size
    FROM candidates
    GROUP BY operation, municipality, sqm_built, rooms, bathrooms, price_eur
    HAVING count(*) > 1 AND sqm_built IS NOT NULL AND rooms IS NOT NULL AND bathrooms IS NOT NULL
)
SELECT m.metric, t.operation, t.tier AS segment, CAST(m.value AS double) AS value
FROM per_tier AS t
JOIN listings_per_tier AS l ON l.operation = t.operation AND l.tier = t.tier
CROSS JOIN UNNEST(
    ARRAY['pairs', 'concurrent_pairs', 'sequential_pairs', 'first_sweep_sequential',
          'listings_in_pairs', 'same_text_pairs', 'same_point_pairs', 'surplus_min', 'surplus_max'],
    ARRAY[t.pairs, t.concurrent_pairs, t.sequential_pairs, t.first_sweep_sequential,
          l.listings_in_pairs, t.same_text_pairs, t.same_point_pairs,
          CAST(ceil(l.listings_in_pairs / 2.0) AS bigint),
          least(t.pairs, l.listings_in_pairs - 1)]
) AS m (metric, value)
UNION ALL
SELECT 'clusters', operation, 'A', CAST(count(*) AS double) FROM tier_a_clusters GROUP BY operation
UNION ALL
SELECT 'surplus', operation, 'A', CAST(sum(size - 1) AS double) FROM tier_a_clusters GROUP BY operation
UNION ALL
SELECT 'cluster_size:' || IF(size >= 5, '5+', CAST(size AS varchar)), operation, 'A', CAST(count(*) AS double)
FROM tier_a_clusters GROUP BY operation, IF(size >= 5, '5+', CAST(size AS varchar))
UNION ALL
SELECT 'price_gap:' || q.label, g.operation, 'A0', round(q.gap, 4)
FROM (
    SELECT operation, approx_percentile(gap, ARRAY[0.1, 0.25, 0.5, 0.75, 0.9]) AS gaps
    FROM pairs GROUP BY operation
) AS g
CROSS JOIN UNNEST(ARRAY['p10', 'p25', 'p50', 'p75', 'p90'], g.gaps) AS q (label, gap)
UNION ALL
SELECT 'pairs_within_gap:' || CAST(w.pct AS varchar) || '%', p.operation, 'A0',
       CAST(count_if(p.gap <= w.pct / 100.0) AS double)
FROM pairs AS p CROSS JOIN UNNEST(ARRAY[0, 1, 5, 10, 20]) AS w (pct)
GROUP BY p.operation, w.pct
ORDER BY metric, operation, segment
