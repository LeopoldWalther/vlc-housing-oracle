"""The landing contract is a file both repositories pin by digest; a change is a new version."""

import hashlib
import unittest
from pathlib import Path

from src.contracts import landing_v1_columns, landing_v3_columns, landing_v3_sha256

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = REPO_ROOT / "contracts" / "landing" / "v1" / "schema.json"

# The acquisition repository pins the same value. A different digest means the copy drifted: never
# edit v1, add landing/v2 beside it.
PINNED_SHA256 = "f52989ab75f2321393082ea882a2b88c5630b51d22c71ff8af44335092369b72"


class TestLandingContract(unittest.TestCase):
    def test_schema_file_matches_the_pinned_digest(self) -> None:
        digest = hashlib.sha256(SCHEMA_FILE.read_bytes()).hexdigest()

        self.assertEqual(PINNED_SHA256, digest)


# Pinned by the acquisition repository since its FEATURE-016; v3 never changes, a change is v4.
PINNED_V3_SHA256 = "0ccda5843a11ec12b6d6ecf8cb9f8301c0df41a3564122b4c4bd78ce0672e61b"


class TestLandingContractV3(unittest.TestCase):
    def test_schema_file_matches_the_pinned_digest(self) -> None:
        self.assertEqual(PINNED_V3_SHA256, landing_v3_sha256())

    def test_every_table_is_v1_plus_run_id_after_source(self) -> None:
        v1, v3 = landing_v1_columns(), landing_v3_columns()

        self.assertEqual(list(v1), list(v3))
        for table, columns in v1.items():
            with self.subTest(table=table):
                expected = [columns[0], ("run_id", "VARCHAR"), *columns[1:]]
                self.assertEqual(expected, v3[table])


if __name__ == "__main__":
    unittest.main()
