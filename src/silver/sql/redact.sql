-- Redaction r2: Spanish phone numbers (with or without +34 / 0034, or a 34 glued to the number,
-- which r1 missed) and an honorific followed by a name. The glued 34 has no word boundary, so it
-- also takes a number written straight after a word. 'g' replaces every match; without it DuckDB
-- replaces only the first. A pattern change is a new redaction version, recorded per row.
CREATE OR REPLACE TEMP MACRO redact_personal(text) AS
    regexp_replace(
        regexp_replace(
            text,
            '(\+34[ .-]?|0034[ .-]?|34|\b)[6789]([ .-]?[0-9]){8}',
            '[phone]',
            'g'
        ),
        '\b(Sr|Sra|Srta|Don|Doña|Dña)\.?\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+',
        '[name]',
        'g'
    )
