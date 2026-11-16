-- One row per row of silver_listing_text: what the portal's features state about the listing.
-- A label is true when one of the listing's wordings has that label as its target in
-- feature_registry, and false otherwise, never null. It is the portal's statement, an inferred
-- label beside the facts, not a verified fact, and it does not depend on the operation.
WITH stated AS (
    SELECT DISTINCT
        text.source,
        text.source_listing_id,
        registry.target
    FROM silver_listing_text AS text
    CROSS JOIN unnest(text.features) AS unnested(feature)
    JOIN feature_registry AS registry
        ON registry.pattern_sha256 = sha256(feature_wording(unnested.feature))
)
SELECT
    text.source,
    text.source_listing_id,
    coalesce(bool_or(stated.target = 'is_tenant_occupied'), false) AS is_tenant_occupied,
    coalesce(bool_or(stated.target = 'is_bare_ownership'), false) AS is_bare_ownership,
    getvariable('transformation_version') AS transformation_version
FROM silver_listing_text AS text
LEFT JOIN stated
    ON stated.source = text.source
    AND stated.source_listing_id = text.source_listing_id
GROUP BY text.source, text.source_listing_id
ORDER BY text.source, text.source_listing_id
