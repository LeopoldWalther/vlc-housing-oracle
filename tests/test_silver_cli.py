"""The operator runs a rebuild from the command line; the catalog comes from their environment."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.silver.rebuild import SILVER_TABLES
from src.synthetic.landing import generate_landing

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestRebuildCli(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.landing = self.root / "landing"
        self.landing.mkdir()
        generate_landing(self.landing, seed=1)
        # PYICEBERG_HOME keeps a developer's own ~/.pyiceberg.yaml out of the test.
        self.environment = {
            name: value for name, value in os.environ.items() if not name.startswith("PYICEBERG_")
        } | {
            "PYICEBERG_HOME": str(self.root),
            "PYICEBERG_CATALOG__TEST__URI": f"sqlite:///{self.root}/catalog.db",
            "PYICEBERG_CATALOG__TEST__WAREHOUSE": f"file://{self.root}",
        }

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "src.silver.rebuild", *arguments],
            cwd=REPO_ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_rebuild_prints_each_table_with_rows_and_snapshot(self) -> None:
        completed = self.run_cli("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(0, completed.returncode, completed.stderr)
        for table in SILVER_TABLES:
            with self.subTest(table=table):
                self.assertRegex(completed.stdout, rf"(?m)^{table}: \d+ rows, snapshot \d+$")

    def test_a_missing_landing_directory_is_a_usage_error(self) -> None:
        completed = self.run_cli("--landing", str(self.root / "absent"), "--catalog", "test")

        self.assertEqual(2, completed.returncode)
        self.assertIn("absent", completed.stderr)

    def test_a_non_conforming_landing_fails_naming_the_table(self) -> None:
        (self.landing / "walk.parquet").unlink()

        completed = self.run_cli("--landing", str(self.landing), "--catalog", "test")

        self.assertEqual(1, completed.returncode)
        self.assertIn("walk", completed.stderr)


if __name__ == "__main__":
    unittest.main()
