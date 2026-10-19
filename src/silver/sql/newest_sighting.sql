-- Landing v3 keeps every collection run's rows; across runs nothing is deduplicated. Silver keeps
-- the newest sighting of each record, which is the row v1's merge kept: within one listing-day of
-- one area the latest observation, within one detail record the latest re-derivation (a replay
-- keeps the capture's observed_at and gets a newer ingested_at). "Latest run" means the latest
-- run_started_at, never the run_id string, because import-<date> ids sort after every timestamp.
CREATE OR REPLACE TEMP VIEW newest_observation AS
SELECT observation.*
FROM landing_observation AS observation
LEFT JOIN landing_runs AS runs USING (run_id)
QUALIFY row_number() OVER (
    PARTITION BY observation.source, observation.area, observation.source_listing_id,
        observation.observed_date
    ORDER BY
        observation.observed_at DESC,
        observation.ingested_at DESC NULLS LAST,
        runs.run_started_at DESC NULLS LAST,
        observation.run_id DESC
) = 1;

CREATE OR REPLACE TEMP VIEW newest_listing_detail AS
SELECT detail.*
FROM landing_listing_detail AS detail
LEFT JOIN landing_runs AS runs USING (run_id)
QUALIFY row_number() OVER (
    PARTITION BY detail.source, detail.area, detail.source_listing_id, detail.observed_at
    ORDER BY
        detail.ingested_at DESC NULLS LAST,
        runs.run_started_at DESC NULLS LAST,
        detail.run_id DESC
) = 1;

-- The text of exactly the detail records kept above, from the same run, so a detail and its text
-- can never come from two different re-derivations.
CREATE OR REPLACE TEMP VIEW newest_listing_text AS
SELECT text.*
FROM landing_listing_text AS text
SEMI JOIN newest_listing_detail AS detail
    ON text.source = detail.source
    AND text.run_id = detail.run_id
    AND text.area = detail.area
    AND text.source_listing_id = detail.source_listing_id
    AND text.observed_at = detail.observed_at;
