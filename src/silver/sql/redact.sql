-- Redaction r1: Spanish phone numbers (with or without +34 / 0034) and an honorific followed by a
-- name. 'g' replaces every match; without it DuckDB replaces only the first. A pattern change is a
-- new redaction version, recorded per row.
CREATE OR REPLACE TEMP MACRO redact_personal(text) AS
    regexp_replace(
        regexp_replace(
            text,
            '(\+34[ .-]?|0034[ .-]?|\b)[6789]([ .-]?[0-9]){8}',
            '[phone]',
            'g'
        ),
        '\b(Sr|Sra|Srta|Don|Doña|Dña)\.?\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+',
        '[name]',
        'g'
    )
