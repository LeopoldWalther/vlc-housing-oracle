-- One row per inferred event of a listing. A sighting is a row of silver_observation, an empty
-- card included; the gap of a sighting runs to the listing's next sighting, open-ended without
-- one. The listing disappeared on the first evidence day of its home area inside the gap, and
-- reappeared on the day of the sighting that ends such a gap. Disappearance is observed absence,
-- never a sale or a rental.
--
-- Home area: the area of the listing's latest sighting up to that day that is a real card. An
-- empty card names the area of a search that happened to show it, not where the listing lives.
-- Evidence day: an area's day with a walk that
--   - is complete: an aborted walk proves nothing about the listings it did not reach;
--   - has is_replay false, null is not false: a replay re-reads old pages;
--   - starts and ends on one UTC date: a walk across midnight cannot say on which day it missed.
WITH sighting AS (
    SELECT
        source,
        source_listing_id,
        observed_date,
        last_value(CASE WHEN NOT is_empty_card THEN area END IGNORE NULLS) OVER (
            PARTITION BY source, source_listing_id ORDER BY observed_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS home_area,
        lead(observed_date) OVER next_sighting AS next_seen_date,
        lead(area) OVER next_sighting AS next_seen_area,
        lead(run_id) OVER next_sighting AS next_seen_run_id
    FROM silver_observation
    WINDOW next_sighting AS (PARTITION BY source, source_listing_id ORDER BY observed_date)
),
evidence AS (
    SELECT
        source,
        area,
        observed_date,
        -- The run that started first names the day, whatever its id's spelling.
        arg_min(run_id, run_started_at) AS run_id
    FROM silver_walk
    WHERE complete IS true
        AND is_replay IS false
        AND CAST(run_started_at AS DATE) = CAST(run_ended_at AS DATE)
    GROUP BY source, area, observed_date
),
gap AS (
    SELECT
        sighting.source,
        sighting.source_listing_id,
        sighting.home_area AS area,
        sighting.observed_date AS last_seen_date,
        sighting.next_seen_date,
        sighting.next_seen_area,
        sighting.next_seen_run_id,
        min(evidence.observed_date) AS event_date
    FROM sighting
    JOIN evidence
        ON evidence.source = sighting.source
        AND evidence.area = sighting.home_area
        AND evidence.observed_date > sighting.observed_date
        AND (sighting.next_seen_date IS NULL OR evidence.observed_date < sighting.next_seen_date)
    GROUP BY ALL
)
SELECT
    gap.source,
    gap.source_listing_id,
    'disappeared' AS event_type,
    gap.event_date,
    gap.area,
    evidence.run_id,
    gap.last_seen_date,
    getvariable('transformation_version') AS transformation_version
FROM gap
JOIN evidence
    ON evidence.source = gap.source
    AND evidence.area = gap.area
    AND evidence.observed_date = gap.event_date
UNION ALL
SELECT
    source,
    source_listing_id,
    'reappeared' AS event_type,
    next_seen_date AS event_date,
    next_seen_area AS area,
    next_seen_run_id AS run_id,
    last_seen_date,
    getvariable('transformation_version') AS transformation_version
FROM gap
WHERE next_seen_date IS NOT NULL
ORDER BY source, source_listing_id, event_date, event_type
