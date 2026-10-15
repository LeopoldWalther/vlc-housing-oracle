"""The landing contract this repository reads, as declared in ``contracts/landing/v1``."""

import hashlib
import json
from pathlib import Path

LANDING_V1_SCHEMA = (
    Path(__file__).resolve().parents[1] / "contracts" / "landing" / "v1" / "schema.json"
)


def landing_v1_columns() -> dict[str, list[tuple[str, str]]]:
    """Map each landing table to its ``(column_name, duckdb_type)`` list, in contract order."""
    schema = json.loads(LANDING_V1_SCHEMA.read_text(encoding="utf-8"))
    return {
        table: [(column["name"], column["type"]) for column in spec["columns"]]
        for table, spec in schema["tables"].items()
    }


def landing_v1_sha256() -> str:
    """Return the SHA-256 of the contract file, which identifies the contract version."""
    return hashlib.sha256(LANDING_V1_SCHEMA.read_bytes()).hexdigest()
