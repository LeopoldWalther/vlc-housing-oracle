-- A wording is a feature string with every number replaced by '#', trimmed and lower-cased, so
-- that "80 m² construidos" and "125 m² construidos" are one wording. A number may carry
-- thousands and decimal separators. The 'g' option replaces every number, not the first only.
CREATE OR REPLACE TEMP MACRO feature_wording(feature) AS
    lower(regexp_replace(trim(feature), '[0-9]+([.,][0-9]+)*', '#', 'g'));
