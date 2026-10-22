# The Glue database is the Iceberg catalog's namespace for Silver. Its name must equal NAMESPACE in
# src/silver/rebuild.py. Tables inside it are created and committed by the rebuild, not by
# Terraform, so a rebuild never shows up as drift here.
resource "aws_glue_catalog_database" "silver" {
  name         = "vlc_housing_silver"
  description  = "Silver Iceberg tables maintained incrementally from landing v3 (see docs/silver-rebuild.md)."
  location_uri = "s3://${var.lake_bucket}/silver/"
}
