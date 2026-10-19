"""The landing contracts this repository reads, as declared in ``contracts/landing/v<n>``."""

import hashlib
import json
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts" / "landing"
LANDING_V1_SCHEMA = CONTRACTS / "v1" / "schema.json"
LANDING_V3_SCHEMA = CONTRACTS / "v3" / "schema.json"


def landing_v1_columns() -> dict[str, list[tuple[str, str]]]:
    """Map each landing v1 table to its ``(column_name, duckdb_type)`` list, in contract order."""
    return _columns(LANDING_V1_SCHEMA)


def landing_v1_sha256() -> str:
    """Return the SHA-256 of the v1 contract file, which identifies that contract version."""
    return _sha256(LANDING_V1_SCHEMA)


def landing_v3_columns() -> dict[str, list[tuple[str, str]]]:
    """Map each landing v3 table to its ``(column_name, duckdb_type)`` list, in contract order."""
    return _columns(LANDING_V3_SCHEMA)


def landing_v3_sha256() -> str:
    """Return the SHA-256 of the v3 contract file, which identifies that contract version."""
    return _sha256(LANDING_V3_SCHEMA)


def _columns(schema_file: Path) -> dict[str, list[tuple[str, str]]]:
    schema = json.loads(schema_file.read_text(encoding="utf-8"))
    return {
        table: [(column["name"], column["type"]) for column in spec["columns"]]
        for table, spec in schema["tables"].items()
    }


def _sha256(schema_file: Path) -> str:
    return hashlib.sha256(schema_file.read_bytes()).hexdigest()
