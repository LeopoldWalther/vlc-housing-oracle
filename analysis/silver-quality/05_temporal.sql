-- Temporal quality of Silver observations (FEATURE-005, task 2.5).
-- Collection rhythm:
--   collection_days, last_collection_day_offset   days with any walk; last day minus RAMP_UP_END
--   area_revisit_days:p<nn>       days between consecutive complete walks of one area
--   listing_revisit_days:p<nn>    days between consecutive observations of one listing
-- Runs (from landing_ingest): runs, replay_runs, import_runs, runs_without_observation, and
--   runs_without_observation_after_import: those not covered by an import run's days, i.e. runs
--   that delivered nothing themselves; runs_without_result_count_after_import: of those, runs
--   whose walks report no search results at all.
-- Missed listings: for each listing and each day its area was walked completely strictly between
--   its first and last sighting, whether it was observed that day:
--   expected_sightings, missed_sightings, listings_missed_at_least_once
-- After the ramp-up (first_seen > RAMP_UP_END, or after the area's own first walk):
--   new_listings:week <yyyy-ww>   new listings per ISO week
--   fresh_listings                listings first seen after the ramp-up
--   ended_listings                last seen before CENSOR_START and absent from a later complete
--                                 walk of their area; a calendar gap alone never ends a listing
--   lifetime_days:p<nn>           first to last sighting of ended listings
-- Price changes, from detail records and from search-result sightings:
--   listings_with_repeat_detail, price_changed, price_down, price_up   (detail)
--   listings_with_repeat_sighting, sighting_price_changed, sighting_price_down,
--   sighting_price_up                                                   (search results)
-- Aggregates only. Snapshot set of 2026-10-31.
WITH
walk_s AS (SELECT * FROM walk FOR VERSION AS OF 7332850479666614547),
observation_s AS (SELECT * FROM observation FOR VERSION AS OF 1037665036251468126),
listing_detail_s AS (SELECT * FROM listing_detail FOR VERSION AS OF 1248122765974686753),
listing_s AS (SELECT * FROM listing FOR VERSION AS OF 2497744844056595693),
landing_ingest_s AS (SELECT * FROM landing_ingest FOR VERSION AS OF 7987848718630309265),
params AS (
    SELECT
        DATE '2026-09-30' AS ramp_up_end,
        (SELECT max(observed_date) FROM walk_s) - INTERVAL '7' DAY AS censor_start
),
area_first_walk AS (
    SELECT area, min(observed_date) AS first_walk FROM walk_s GROUP BY area
),
complete_walk_days AS (
    SELECT DISTINCT area, observed_date FROM walk_s WHERE complete AND NOT is_replay
),
area_gaps AS (
    SELECT date_diff('day', lag(observed_date) OVER (PARTITION BY area ORDER BY observed_date),
                     observed_date) AS days
    FROM complete_walk_days
),
listing_days AS (
    SELECT DISTINCT source, source_listing_id, observed_date FROM observation_s
),
listing_gaps AS (
    SELECT date_diff('day', lag(observed_date) OVER (
                         PARTITION BY source, source_listing_id ORDER BY observed_date),
                     observed_date) AS days
    FROM listing_days
),
expected AS (
    SELECT l.source, l.source_listing_id, w.observed_date,
           o.observed_date IS NOT NULL AS seen
    FROM listing_s AS l
    JOIN complete_walk_days AS w
        ON w.area = l.area AND w.observed_date > l.first_seen AND w.observed_date < l.last_seen
    LEFT JOIN listing_days AS o
        ON o.source = l.source AND o.source_listing_id = l.source_listing_id
        AND o.observed_date = w.observed_date
),
fresh AS (
    SELECT
        l.*,
        l.last_seen < p.censor_start
            AND EXISTS (SELECT 1 FROM complete_walk_days AS w
                        WHERE w.area = l.area AND w.observed_date > l.last_seen) AS ended
    FROM listing_s AS l
    JOIN area_first_walk AS a ON a.area = l.area
    CROSS JOIN params AS p
    WHERE l.first_seen > greatest(p.ramp_up_end, a.first_walk)
),
detail_prices AS (
    SELECT source, source_listing_id,
           count(*) AS records,
           count(DISTINCT price_eur) AS prices,
           min_by(price_eur, observed_at) AS first_price,
           max_by(price_eur, observed_at) AS last_price
    FROM listing_detail_s
    GROUP BY source, source_listing_id
),
sighting_prices AS (
    SELECT source, source_listing_id,
           count(*) AS sightings,
           count(DISTINCT price_eur) AS prices,
           min_by(price_eur, observed_at) AS first_price,
           max_by(price_eur, observed_at) AS last_price
    FROM observation_s
    WHERE NOT is_empty_card AND price_eur IS NOT NULL
    GROUP BY source, source_listing_id
)
SELECT 'collection_days' AS metric, 'all' AS operation, 'all' AS segment,
       CAST(count(DISTINCT observed_date) AS double) AS value
FROM walk_s
UNION ALL
SELECT 'last_collection_day_offset', 'all', 'all',
       CAST(date_diff('day', p.ramp_up_end, max(w.observed_date)) AS double)
FROM walk_s AS w CROSS JOIN params AS p GROUP BY p.ramp_up_end
UNION ALL
SELECT 'area_revisit_days:' || q.label, 'all', 'all', q.days
FROM (SELECT approx_percentile(CAST(days AS double), ARRAY[0.1, 0.25, 0.5, 0.75, 0.9]) AS d
      FROM area_gaps WHERE days IS NOT NULL) AS g
CROSS JOIN UNNEST(ARRAY['p10', 'p25', 'p50', 'p75', 'p90'], g.d) AS q (label, days)
UNION ALL
SELECT 'listing_revisit_days:' || q.label, 'all', 'all', q.days
FROM (SELECT approx_percentile(CAST(days AS double), ARRAY[0.1, 0.25, 0.5, 0.75, 0.9]) AS d
      FROM listing_gaps WHERE days IS NOT NULL) AS g
CROSS JOIN UNNEST(ARRAY['p10', 'p25', 'p50', 'p75', 'p90'], g.d) AS q (label, days)
UNION ALL
SELECT 'runs', 'all', 'all', CAST(count(*) AS double) FROM landing_ingest_s
UNION ALL
SELECT 'replay_runs', 'all', 'all', CAST(count_if(is_replay) AS double) FROM landing_ingest_s
UNION ALL
SELECT 'runs_without_observation', 'all', 'all',
       CAST(count_if(cardinality(coalesce(observed_dates_observation, ARRAY[])) = 0) AS double)
FROM landing_ingest_s
UNION ALL
SELECT 'import_runs', 'all', 'all', CAST(count_if(regexp_like(run_id, '^import')) AS double)
FROM landing_ingest_s
UNION ALL
SELECT 'runs_without_observation_after_import', 'all', 'all', CAST(count(*) AS double)
FROM landing_ingest_s
WHERE cardinality(coalesce(observed_dates_observation, ARRAY[])) = 0
    AND date(run_started_at) > (
        SELECT max(d) FROM landing_ingest_s CROSS JOIN UNNEST(observed_dates_observation) AS u (d)
        WHERE regexp_like(run_id, '^import'))
UNION ALL
SELECT 'runs_without_result_count_after_import', 'all', 'all', CAST(count(*) AS double)
FROM landing_ingest_s AS i
WHERE cardinality(coalesce(i.observed_dates_observation, ARRAY[])) = 0
    AND date(i.run_started_at) > (
        SELECT max(d) FROM landing_ingest_s CROSS JOIN UNNEST(observed_dates_observation) AS u (d)
        WHERE regexp_like(run_id, '^import'))
    AND NOT EXISTS (SELECT 1 FROM walk_s AS w WHERE w.run_id = i.run_id AND w.result_count > 0)
UNION ALL
SELECT 'expected_sightings', 'all', 'all', CAST(count(*) AS double) FROM expected
UNION ALL
SELECT 'missed_sightings', 'all', 'all', CAST(count_if(NOT seen) AS double) FROM expected
UNION ALL
SELECT 'listings_missed_at_least_once', 'all', 'all',
       CAST(count(DISTINCT IF(NOT seen, source_listing_id)) AS double)
FROM expected
UNION ALL
SELECT 'new_listings:week ' || format_datetime(first_seen, 'xxxx-ww'), operation, 'all',
       CAST(count(*) AS double)
FROM fresh GROUP BY format_datetime(first_seen, 'xxxx-ww'), operation
UNION ALL
SELECT 'fresh_listings', operation, 'all', CAST(count(*) AS double) FROM fresh GROUP BY operation
UNION ALL
SELECT 'ended_listings', operation, 'all', CAST(count_if(ended) AS double) FROM fresh GROUP BY operation
UNION ALL
SELECT 'lifetime_days:' || q.label, f.operation, 'all', q.days
FROM (SELECT operation,
             approx_percentile(CAST(date_diff('day', first_seen, last_seen) + 1 AS double),
                               ARRAY[0.25, 0.5, 0.75, 0.9]) AS d
      FROM fresh WHERE ended GROUP BY operation) AS f
CROSS JOIN UNNEST(ARRAY['p25', 'p50', 'p75', 'p90'], f.d) AS q (label, days)
UNION ALL
SELECT m.metric, 'all', 'all', CAST(m.n AS double)
FROM (
    SELECT count_if(records >= 2) AS repeat_detail,
           count_if(records >= 2 AND prices > 1) AS changed,
           count_if(records >= 2 AND last_price < first_price) AS down,
           count_if(records >= 2 AND last_price > first_price) AS up
    FROM detail_prices
) AS c
CROSS JOIN UNNEST(
    ARRAY['listings_with_repeat_detail', 'price_changed', 'price_down', 'price_up'],
    ARRAY[c.repeat_detail, c.changed, c.down, c.up]
) AS m (metric, n)
UNION ALL
SELECT m.metric, 'all', 'all', CAST(m.n AS double)
FROM (
    SELECT count_if(sightings >= 2) AS repeat_sighting,
           count_if(sightings >= 2 AND prices > 1) AS changed,
           count_if(sightings >= 2 AND last_price < first_price) AS down,
           count_if(sightings >= 2 AND last_price > first_price) AS up
    FROM sighting_prices
) AS c
CROSS JOIN UNNEST(
    ARRAY['listings_with_repeat_sighting', 'sighting_price_changed', 'sighting_price_down',
          'sighting_price_up'],
    ARRAY[c.repeat_sighting, c.changed, c.down, c.up]
) AS m (metric, n)
ORDER BY metric, operation, segment
