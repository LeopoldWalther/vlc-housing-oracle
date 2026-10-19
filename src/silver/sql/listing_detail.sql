-- Every detail record, newest sighting per record: the history the listing recompute reads, so a
-- night never rereads all of landing. Text stays in landing; it enters Silver only redacted.
SELECT
    *,
    getvariable('transformation_version') AS transformation_version
FROM newest_listing_detail
ORDER BY source_listing_id, observed_at
