-- One row per feature wording seen in silver_listing_text: how many listings carry it, which
-- column or label carries what it says, and how many of those listings lack that value.
--
-- A wording is feature_wording(feature): the portal's feature string with numbers replaced.
-- Its target comes from feature_registry and is null for a wording that is not registered,
-- which is the signal to read it and add it to the registry.
-- A target that is a column of silver_listing is missing for a listing when the column's value,
-- as text, is null, 'unknown' or an empty list, or when the listing has no row there: the
-- portal states the fact and the column does not hold it. Labels and the target for wordings
-- that no column carries have no such count. Every column of silver_listing except the key is
-- unpivoted, so no column name is written here.
WITH listing_wording AS (
    SELECT DISTINCT
        text.source,
        text.source_listing_id,
        feature_wording(unnested.feature) AS pattern
    FROM silver_listing_text AS text, unnest(text.features) AS unnested(feature)
    WHERE unnested.feature IS NOT NULL
),
attribute AS (
    SELECT source, source_listing_id, name, value
    FROM (
        SELECT
            source,
            source_listing_id,
            CAST(COLUMNS(* EXCLUDE (source, source_listing_id)) AS VARCHAR)
        FROM silver_listing
    ) UNPIVOT INCLUDE NULLS (
        value FOR name IN (COLUMNS(* EXCLUDE (source, source_listing_id)))
    )
),
judged AS (
    SELECT
        wording.pattern,
        registry.target,
        registry.target IS NOT NULL
        AND NOT list_contains(getvariable('feature_targets_without_column'), registry.target)
            AS target_is_column,
        attribute.value IS NULL OR attribute.value IN ('unknown', '[]') AS target_missing
    FROM listing_wording AS wording
    LEFT JOIN feature_registry AS registry
        ON registry.pattern_sha256 = sha256(wording.pattern)
    LEFT JOIN attribute
        ON attribute.source = wording.source
        AND attribute.source_listing_id = wording.source_listing_id
        AND attribute.name = registry.target
)
SELECT
    pattern,
    CAST(count(*) AS INTEGER) AS listings,
    any_value(target) AS target,
    IF(
        bool_and(target_is_column),
        CAST(count(*) FILTER (WHERE target_missing) AS INTEGER),
        NULL
    ) AS listings_target_missing,
    getvariable('transformation_version') AS transformation_version
FROM judged
GROUP BY pattern
ORDER BY listings DESC, pattern
