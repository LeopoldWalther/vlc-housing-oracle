"""The landing contract is a file both repositories pin by digest; a change is a new version."""

import hashlib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = REPO_ROOT / "contracts" / "landing" / "v1" / "schema.json"

# The acquisition repository pins the same value. A different digest means the copy drifted: never
# edit v1, add landing/v2 beside it.
PINNED_SHA256 = "f52989ab75f2321393082ea882a2b88c5630b51d22c71ff8af44335092369b72"


class TestLandingContract(unittest.TestCase):
    def test_schema_file_matches_the_pinned_digest(self) -> None:
        digest = hashlib.sha256(SCHEMA_FILE.read_bytes()).hexdigest()

        self.assertEqual(PINNED_SHA256, digest)


if __name__ == "__main__":
    unittest.main()
