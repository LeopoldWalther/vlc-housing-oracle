-- One row per listing seen in a search result or a detail page. Attributes come from the chosen
-- detail record and are null without one; the seen columns count every deduplicated observation,
-- padding included, and are null for a listing never seen in a search result.
WITH seen AS (
    SELECT
        source,
        source_listing_id,
        min(observed_date) AS first_seen,
        max(observed_date) AS last_seen,
        count(*)::INTEGER AS days_observed
    FROM silver_observation
    GROUP BY source, source_listing_id
)
SELECT
    coalesce(detail.source, seen.source) AS source,
    coalesce(detail.source_listing_id, seen.source_listing_id) AS source_listing_id,
    detail.* EXCLUDE (source, source_listing_id) RENAME (observed_at AS detail_observed_at),
    seen.first_seen,
    seen.last_seen,
    seen.days_observed,
    getvariable('transformation_version') AS transformation_version
FROM chosen_detail AS detail
FULL OUTER JOIN seen
    ON detail.source = seen.source AND detail.source_listing_id = seen.source_listing_id
ORDER BY source_listing_id
