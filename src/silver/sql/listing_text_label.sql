-- One row per row of listing_text with the model's labels of its redacted description (ADR 0005).
-- A label is the newest stored answer of getvariable('label_version') for sha256(description),
-- the hash the labeller keys its answers by; it is taken only for a sale listing whose description
-- is not blank, as the labeller sends no other. Every other row carries five nulls.
WITH newest_answer AS (
    SELECT *
    FROM text_label_answer
    WHERE label_version = getvariable('label_version')
    QUALIFY row_number() OVER (PARTITION BY text_sha256 ORDER BY labeled_at DESC) = 1
),
labelled AS (
    SELECT
        text.source,
        text.source_listing_id,
        CASE
            WHEN listing.operation = 'sale'
                AND trim(text.description, E' \t\n\r\f\v') <> ''
                THEN answer.text_sha256
        END AS answer_key
    FROM text_label_text AS text
    LEFT JOIN text_label_listing AS listing
        ON listing.source = text.source AND listing.source_listing_id = text.source_listing_id
    LEFT JOIN newest_answer AS answer
        ON answer.text_sha256 = sha256(text.description)
)
SELECT
    labelled.source,
    labelled.source_listing_id,
    answer.renovation_state,
    answer.is_auction,
    answer.label_version,
    answer.model_id,
    answer.labeled_at,
    getvariable('transformation_version') AS transformation_version
FROM labelled
LEFT JOIN newest_answer AS answer ON answer.text_sha256 = labelled.answer_key
ORDER BY labelled.source, labelled.source_listing_id
