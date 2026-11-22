-- The texts the labeller still has to send under getvariable('label_version'): every distinct,
-- non-blank description of a sale listing that has no stored answer of that version and is in
-- no open batch of that version. The hash is DuckDB's sha256 of the UTF-8 bytes, the same as
-- Silver's join key.
WITH sale_text AS (
    SELECT DISTINCT t.description
    FROM pending_listing_text AS t
    JOIN pending_listing AS l
        ON l.source = t.source AND l.source_listing_id = t.source_listing_id
    WHERE l.operation = 'sale'
        AND t.description IS NOT NULL
        AND trim(t.description, E' \t\n\r\f\v') <> ''
),
hashed AS (
    SELECT sha256(description) AS text_sha256, description FROM sale_text
)
SELECT h.text_sha256, h.description
FROM hashed AS h
WHERE NOT EXISTS (
        SELECT 1 FROM pending_stored AS s
        WHERE s.text_sha256 = h.text_sha256 AND s.label_version = getvariable('label_version')
    )
    AND NOT EXISTS (
        SELECT 1 FROM pending_open_texts AS o
        WHERE o.text_sha256 = h.text_sha256 AND o.label_version = getvariable('label_version')
    )
ORDER BY h.text_sha256
