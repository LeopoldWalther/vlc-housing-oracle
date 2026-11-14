-- One row per inferred event of a listing. A sighting is a row of silver_observation; the gap of
-- a sighting runs to the listing's next sighting, open-ended without one. The listing disappeared
-- on the first evidence day of its area inside the gap: a day with a complete live walk of that
-- area that did not see it. Disappearance is observed absence, never a sale or a rental.
WITH sighting AS (
    SELECT
        source,
        source_listing_id,
        observed_date,
        area,
        lead(observed_date) OVER (
            PARTITION BY source, source_listing_id ORDER BY observed_date
        ) AS next_seen_date
    FROM silver_observation
),
evidence AS (
    SELECT
        source,
        area,
        observed_date,
        min(run_id) AS run_id
    FROM silver_walk
    WHERE complete AND NOT is_replay
    GROUP BY source, area, observed_date
),
gap AS (
    SELECT
        sighting.source,
        sighting.source_listing_id,
        sighting.area,
        sighting.observed_date AS last_seen_date,
        min(evidence.observed_date) AS event_date
    FROM sighting
    JOIN evidence
        ON evidence.source = sighting.source
        AND evidence.area = sighting.area
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
