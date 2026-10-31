-- What redaction r1 leaves behind in listing_text (FEATURE-005, task 2.6).
-- r1 (src/silver/sql/redact.sql) replaces (\+34[ .-]?|0034[ .-]?|\b)[6789]([ .-]?[0-9]){8} with
-- [phone] and an honorific plus a capitalised word with [name]. Per text column (segment), the
-- number of listings whose redacted text still matches:
--   r1_phone_residue          r1's own phone pattern: must be 0
--   phone_glued_to_letter     nine phone digits right after a letter ("tel600..."): \b fails
--   phone_glued_country_code  34 plus nine phone digits with nothing between: \b fails
--   phone_inside_longer_number  the nine digits inside a longer digit run, not glued 34: most
--                             likely references or cadastral numbers, not phones
--   email, web_address
--   web_address_spaced        a scheme or www. followed by a space, as when an address is split
--                             to slip past a portal's filter
--   name_not_capitalised      an honorific followed by a lower-case or all-caps word
-- And for context: redacted_phone, redacted_name (texts holding r1's placeholders), texts.
-- Aggregates only; no matched text is returned. Snapshot set of 2026-10-31.
WITH
listing_text_s AS (SELECT * FROM listing_text FOR VERSION AS OF 4512740065342526121),
texts AS (
    SELECT t.segment, t.body
    FROM listing_text_s
    CROSS JOIN UNNEST(
        ARRAY['title', 'location', 'description', 'features'],
        ARRAY[title, location, description, array_join(features, ' | ')]
    ) AS t (segment, body)
    WHERE t.body IS NOT NULL
),
patterns AS (
    SELECT * FROM (VALUES
        ('r1_phone_residue', '(\+34[ .-]?|0034[ .-]?|\b)[6789]([ .-]?[0-9]){8}'),
        ('phone_glued_to_letter', '[A-Za-zÁÉÍÓÚÑáéíóúñ_][6789]([ .-]?[0-9]){8}(?![0-9])'),
        ('phone_glued_country_code', '(?<![0-9])34[6789][0-9]{8}(?![0-9])'),
        ('phone_inside_longer_number', '(?<!(?<![0-9])34)(?<=[0-9])[6789]([ .-]?[0-9]){8}'),
        ('email', '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
        ('web_address', '(?i)(https?://|www\.)\S+'),
        ('web_address_spaced', '(?i)(https?://|www\.)\s'),
        ('name_not_capitalised', '\b(Sr|Sra|Srta|Don|Doña|Dña)\.?\s+([a-záéíóúñ]+|[A-ZÁÉÍÓÚÑ]{2,})\b'),
        ('redacted_phone', '\[phone\]'),
        ('redacted_name', '\[name\]')
    ) AS p (metric, pattern)
)
SELECT p.metric, 'all' AS operation, t.segment, CAST(count_if(regexp_like(t.body, p.pattern)) AS double) AS value
FROM texts AS t CROSS JOIN patterns AS p
GROUP BY p.metric, t.segment
UNION ALL
SELECT 'texts', 'all', segment, CAST(count(*) AS double) FROM texts GROUP BY segment
ORDER BY metric, operation, segment
