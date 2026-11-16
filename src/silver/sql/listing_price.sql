-- One row per listing and price period.
--
-- A sighting is a priced row of silver_observation that is not an empty card, or a priced row of
-- silver_listing_detail. A listing's sightings are in a total order: by observed_at, then cards
-- before detail records (the detail page is fetched after the card that led to it), then by
-- price_eur, then by prev_price_eur with nulls last; rows equal in all four are interchangeable.
-- A period is a maximal run of consecutive sightings with one price, so a return to an earlier
-- price starts a new period. The current period is the last one: the last price seen, not a
-- statement that the listing is online.
--
-- previous_price_eur is observed: the price of the period before. declared_previous_price_eur is
-- the portal's statement: the prev_price_eur of the period's first sighting, card or detail
-- record, whatever later sightings of the period declare.
WITH sighting AS (
    SELECT source, source_listing_id, observed_at, price_eur, prev_price_eur, 0 AS kind
    FROM silver_observation
    WHERE is_empty_card IS false AND price_eur IS NOT NULL
    UNION ALL
    SELECT source, source_listing_id, observed_at, price_eur, prev_price_eur, 1 AS kind
    FROM silver_listing_detail
    WHERE price_eur IS NOT NULL
),
ordered AS (
    SELECT
        *,
        row_number() OVER listing_order AS position,
        CASE WHEN price_eur IS DISTINCT FROM lag(price_eur) OVER listing_order THEN 1 ELSE 0 END
            AS starts_period
    FROM sighting
    WINDOW listing_order AS (
        PARTITION BY source, source_listing_id
        ORDER BY observed_at, kind, price_eur, prev_price_eur NULLS LAST
    )
),
numbered AS (
    SELECT
        *,
        sum(starts_period) OVER (
            PARTITION BY source, source_listing_id ORDER BY position
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS period
    FROM ordered
),
period AS (
    SELECT
        source,
        source_listing_id,
        CAST(period AS INTEGER) AS period,
        any_value(price_eur) AS price_eur,
        min(observed_at) AS first_observed_at,
        max(observed_at) AS last_observed_at,
        CAST(count(*) AS INTEGER) AS sightings,
        first(prev_price_eur ORDER BY position) AS declared_previous_price_eur
    FROM numbered
    GROUP BY source, source_listing_id, period
)
SELECT
    source,
    source_listing_id,
    period,
    price_eur,
    first_observed_at,
    last_observed_at,
    sightings,
    lag(price_eur) OVER (PARTITION BY source, source_listing_id ORDER BY period)
        AS previous_price_eur,
    declared_previous_price_eur,
    period = max(period) OVER (PARTITION BY source, source_listing_id) AS is_current,
    getvariable('transformation_version') AS transformation_version
FROM period
ORDER BY source, source_listing_id, period
