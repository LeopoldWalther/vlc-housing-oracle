-- One row per listing with a detail record: the census section its coordinates lie in, and with
-- the section its district and municipality, as the statistics office codes them.
--
-- Reads location_listing (Silver listing) and location_sections (one reference vintage: polygons
-- as WKB in EPSG:4326). Longitude is x and latitude is y everywhere: a point is
-- ST_Point(longitude, latitude), and swapped axes give null codes for every listing, not an error.
--
-- A listing belongs to a polygon it lies in or on the border of. Of several such polygons the
-- one with the smallest section code is taken and all three codes come from that one polygon, so
-- the result is one row per listing whatever the polygons do. A listing without coordinates or
-- outside every polygon has null codes: no evidence, not "outside the province".
--
-- reference_vintage names the input and is never null. A new vintage changes rows without a new
-- transformation_version: the input changed, not the rule.
WITH detail AS (
    SELECT source, source_listing_id, latitude, longitude
    FROM location_listing
    WHERE detail_observed_at IS NOT NULL
),
located AS (
    SELECT
        detail.source,
        detail.source_listing_id,
        section.municipality_code,
        section.district_code,
        section.section_code
    FROM detail
    INNER JOIN location_sections AS section
        ON ST_Intersects(
            ST_GeomFromWKB(section.geometry), ST_Point(detail.longitude, detail.latitude)
        )
    QUALIFY row_number() OVER (
        PARTITION BY detail.source, detail.source_listing_id ORDER BY section.section_code
    ) = 1
)
SELECT
    detail.source,
    detail.source_listing_id,
    located.municipality_code,
    located.district_code,
    located.section_code,
    (SELECT any_value(vintage) FROM location_sections) AS reference_vintage,
    getvariable('transformation_version') AS transformation_version
FROM detail
LEFT JOIN located USING (source, source_listing_id)
ORDER BY detail.source, detail.source_listing_id
